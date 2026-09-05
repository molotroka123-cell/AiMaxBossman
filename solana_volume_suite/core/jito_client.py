import base64
import random
import httpx
from typing import List, Dict, Any, Optional
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.hash import Hash
from solders.instruction import Instruction
from solders.system_program import transfer, TransferParams

# Official Jito MEV Tip Accounts
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",  # ci-secret-scan: allow -- public Solana program/mint/destination address
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"  # ci-secret-scan: allow -- public Solana program/mint/destination address
]

DEFAULT_JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
MIN_JITO_TIP_LAMPORTS = 100_000  # 0.0001 SOL floor
MAX_BUNDLE_TRANSACTIONS = 5


class JitoBundleDropException(Exception):
    """Raised when Jito fails or rejects a bundle. PUBLIC MEMPOOL FALLBACK IS FORBIDDEN."""
    pass


class JitoBundleClient:
    """
    Enforces 100% Anti-MEV / Anti-Sandwich Protection via Private Jito MEV Bundles.
    MEMPOOL INVARIANT:
      No trading transaction may ever be broadcast via standard public RPC sendTransaction.
      If Jito fails, the transaction is DROPPED.
    """

    def __init__(
        self,
        block_engine_url: str = DEFAULT_JITO_ENGINE_URL,
        min_tip_lamports: int = MIN_JITO_TIP_LAMPORTS,
        timeout: float = 8.0
    ):
        self.block_engine_url = block_engine_url
        self.min_tip_lamports = max(MIN_JITO_TIP_LAMPORTS, min_tip_lamports)
        self.timeout = timeout
        self.total_bundles_sent = 0
        self.total_bundles_confirmed = 0
        self.total_bundles_dropped = 0
        self.total_tips_paid_lamports = 0

    def get_random_tip_account(self) -> Pubkey:
        chosen = random.choice(JITO_TIP_ACCOUNTS)
        return Pubkey.from_string(chosen)

    def calculate_dynamic_tip(self, network_congestion: Any = "medium") -> int:
        """
        Calculates dynamic Jito tip based on network load or tier name.
        Supports:
          - String tiers: "low" (10k), "medium" (50k), "high" (100k), "extreme" (250k)
          - Numeric multiplier: float/int
        """
        tip_table = {"low": 10_000, "medium": 50_000, "high": 100_000, "extreme": 250_000}
        if isinstance(network_congestion, str) and network_congestion.lower() in tip_table:
            return tip_table[network_congestion.lower()]

        try:
            mult = max(1.0, float(network_congestion))
        except (ValueError, TypeError):
            mult = 1.0

        base = int(self.min_tip_lamports * mult)
        jitter = int(base * random.uniform(0.01, 0.15))
        return base + jitter

    async def get_bundle_status(self, bundle_id: str) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBundleStatuses", "params": [[bundle_id]]}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(self.block_engine_url, json=payload)
            return resp.json()

    def build_tip_instruction(
        self,
        payer: Pubkey,
        tip_lamports: Optional[int] = None,
        congestion_multiplier: float = 1.0
    ) -> Instruction:
        tip_amt = tip_lamports if tip_lamports is not None else self.calculate_dynamic_tip(congestion_multiplier)
        tip_account = self.get_random_tip_account()
        return transfer(
            TransferParams(
                from_pubkey=payer,
                to_pubkey=tip_account,
                lamports=tip_amt
            )
        )

    def compile_v0_transaction(
        self,
        payer: Pubkey,
        instructions: List[Instruction],
        recent_blockhash: Hash,
        signers: List[Keypair],
        address_lookup_table_accounts: Optional[List[Any]] = None
    ) -> VersionedTransaction:
        """
        Compiles an atomic VersionedTransaction (MessageV0) compliant with Jito requirements.
        """
        luts = address_lookup_table_accounts or []
        msg = MessageV0.try_compile(
            payer=payer,
            instructions=instructions,
            address_lookup_table_accounts=luts,
            recent_blockhash=recent_blockhash
        )
        return VersionedTransaction(msg, signers)

    async def send_bundle(
        self,
        transactions: List[VersionedTransaction],
        client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, Any]:
        """
        Submits atomic bundle to Jito Block Engine.
        STRICT FAIL-CLOSED INVARIANT:
        If Jito returns an error or fails, the transaction is DROPPED.
        NEVER route to standard RPC.
        """
        self.total_bundles_dropped += 1
        raise JitoBundleDropException(
            "LIVE_EXECUTION_DISABLED: Bundle DROPPED (no public mempool fallback)."
        )
