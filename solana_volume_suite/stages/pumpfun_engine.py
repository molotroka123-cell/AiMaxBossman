import struct
from typing import Tuple, List, Optional
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.system_program import ID as SYS_PROGRAM_ID
from solders.transaction import VersionedTransaction
from solders.hash import Hash
from spl.token.constants import TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
from core.key_vault import RentReclaimer
from core.jito_client import JitoBundleClient
from core.treasury_guard import SubImpactEngine

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMP_GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
PUMP_FEE_RECIPIENT = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
PUMP_EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
SYSVAR_RENT = Pubkey.from_string("SysvarRent111111111111111111111111111111111")

BUY_DISCRIMINATOR = struct.pack("<Q", 16927863322537952870)
SELL_DISCRIMINATOR = struct.pack("<Q", 12502976635542562355)
MIGRATION_CUTOFF_PCT = 95.0


class MigrationThresholdExceededException(Exception):
    """Raised when bonding curve is >= 95%. Purchases frozen to prevent getting trapped in migration lock."""
    pass


def get_bonding_curve_pda(mint: Pubkey) -> Tuple[Pubkey, int]:
    seeds = [b"bonding-curve", bytes(mint)]
    return Pubkey.find_program_address(seeds, PUMP_PROGRAM)


def get_associated_token_address(wallet: Pubkey, mint: Pubkey) -> Pubkey:
    seeds = [bytes(wallet), bytes(TOKEN_PROGRAM_ID), bytes(mint)]
    pda, _ = Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM_ID)
    return pda


def build_pump_buy_instruction(
    payer: Pubkey,
    mint: Pubkey,
    token_amount: int,
    max_sol_cost_lamports: int
) -> Instruction:
    bonding_curve, _ = get_bonding_curve_pda(mint)
    associated_bonding_curve = get_associated_token_address(bonding_curve, mint)
    associated_user = get_associated_token_address(payer, mint)

    accounts = [
        AccountMeta(pubkey=PUMP_GLOBAL, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FEE_RECIPIENT, is_signer=False, is_writable=True),
        AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_user, is_signer=False, is_writable=True),
        AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYS_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=SYSVAR_RENT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_EVENT_AUTHORITY, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_PROGRAM, is_signer=False, is_writable=False),
    ]

    data = BUY_DISCRIMINATOR + struct.pack("<QQ", token_amount, max_sol_cost_lamports)
    return Instruction(PUMP_PROGRAM, data, accounts)


def build_pump_sell_instruction(
    payer: Pubkey,
    mint: Pubkey,
    token_amount: int,
    min_sol_output_lamports: int
) -> Instruction:
    bonding_curve, _ = get_bonding_curve_pda(mint)
    associated_bonding_curve = get_associated_token_address(bonding_curve, mint)
    associated_user = get_associated_token_address(payer, mint)

    accounts = [
        AccountMeta(pubkey=PUMP_GLOBAL, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_FEE_RECIPIENT, is_signer=False, is_writable=True),
        AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
        AccountMeta(pubkey=bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_bonding_curve, is_signer=False, is_writable=True),
        AccountMeta(pubkey=associated_user, is_signer=False, is_writable=True),
        AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYS_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_EVENT_AUTHORITY, is_signer=False, is_writable=False),
        AccountMeta(pubkey=PUMP_PROGRAM, is_signer=False, is_writable=False),
    ]

    data = SELL_DISCRIMINATOR + struct.pack("<QQ", token_amount, min_sol_output_lamports)
    return Instruction(PUMP_PROGRAM, data, accounts)


class PumpFunEngine:
    """
    Stage 1 Engine: Pump.fun Virtual Bonding Curve volume orchestrator.
    Features:
    - KOTH Push & Micro-Pulse (Photon/BullX high frequency visibility)
    - Sub-Impact order subdivision (<= 1.2% price impact)
    - 95% Migration safeguard threshold
    - Jito-only atomic bundle assembly
    - Rent recovery instruction on total sell
    """

    def __init__(self, jito_client: JitoBundleClient):
        self.jito_client = jito_client

    def validate_curve_safety(self, curve_progress_pct: float, action: str):
        if action.upper() in ["BUY", "KOTH_PULSE"] and curve_progress_pct >= MIGRATION_CUTOFF_PCT:
            raise MigrationThresholdExceededException(
                f"Curve progress is {curve_progress_pct:.2f}% >= {MIGRATION_CUTOFF_PCT}%. "
                "Buying blocked to prevent capital lock during migration to Raydium."
            )

    def assemble_pump_buy_bundle(
        self,
        buyer_kp: Keypair,
        mint: Pubkey,
        amount_sol: float,
        curve_progress_pct: float,
        recent_blockhash: Hash,
        pool_sol_reserve: float = 30.0,
        tip_lamports: Optional[int] = None
    ) -> List[VersionedTransaction]:
        self.validate_curve_safety(curve_progress_pct, "BUY")

        # Slice order if price impact exceeds 1.2%
        slices = SubImpactEngine.enforce_sub_impact_limits(amount_sol, pool_sol_reserve, max_impact_pct=1.2)
        tx_bundle = []

        for idx, slice_sol in enumerate(slices):
            # Estimate tokens: approx 30M tokens per 1 SOL at base
            token_amount = int(slice_sol * 30_000_000 * 10**6)
            max_sol_lamports = int(slice_sol * 1.05 * 10**9)  # 5% max slippage cap

            ix_buy = build_pump_buy_instruction(
                payer=buyer_kp.pubkey(),
                mint=mint,
                token_amount=token_amount,
                max_sol_cost_lamports=max_sol_lamports
            )

            instructions = [ix_buy]
            # Attach Jito Tip to the final transaction in the bundle
            if idx == len(slices) - 1:
                ix_tip = self.jito_client.build_tip_instruction(
                    payer=buyer_kp.pubkey(),
                    tip_lamports=tip_lamports
                )
                instructions.append(ix_tip)

            tx = self.jito_client.compile_v0_transaction(
                payer=buyer_kp.pubkey(),
                instructions=instructions,
                recent_blockhash=recent_blockhash,
                signers=[buyer_kp]
            )
            tx_bundle.append(tx)

        return tx_bundle

    def assemble_pump_sell_bundle(
        self,
        seller_kp: Keypair,
        mint: Pubkey,
        token_amount: int,
        min_sol_lamports: int,
        recent_blockhash: Hash,
        reclaim_rent: bool = True,
        tip_lamports: Optional[int] = None
    ) -> VersionedTransaction:
        ix_sell = build_pump_sell_instruction(
            payer=seller_kp.pubkey(),
            mint=mint,
            token_amount=token_amount,
            min_sol_output_lamports=min_sol_lamports
        )

        instructions = [ix_sell]

        # Rent Reclaimer: If fully exiting position, close ATA to recover 0.002039 SOL
        if reclaim_rent:
            ata = get_associated_token_address(seller_kp.pubkey(), mint)
            ix_close = RentReclaimer.build_close_account_ix(
                token_account=ata,
                destination_sol_wallet=seller_kp.pubkey(),
                owner=seller_kp.pubkey()
            )
            instructions.append(ix_close)

        # Attach anti-MEV Jito Tip
        ix_tip = self.jito_client.build_tip_instruction(
            payer=seller_kp.pubkey(),
            tip_lamports=tip_lamports
        )
        instructions.append(ix_tip)

        return self.jito_client.compile_v0_transaction(
            payer=seller_kp.pubkey(),
            instructions=instructions,
            recent_blockhash=recent_blockhash,
            signers=[seller_kp]
        )
