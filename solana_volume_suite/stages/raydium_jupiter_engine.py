import base64
import httpx
from typing import Dict, Any, List, Optional
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from core.jito_client import JitoBundleClient
from core.treasury_guard import SubImpactEngine

SOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_JUP_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
DEFAULT_JUP_SWAP_URL = "https://quote-api.jup.ag/v6/swap"


class JupiterSwapEngine:
    """
    Stage 2 Engine: Jupiter V6 Post-Migration Aggregator & Raydium Market Maker.
    Routes seamlessly across Raydium AMM v4, CPMM, CLMM, and Meteora.
    """

    def __init__(
        self,
        quote_url: str = DEFAULT_JUP_QUOTE_URL,
        swap_url: str = DEFAULT_JUP_SWAP_URL,
        timeout: float = 8.0
    ):
        self.quote_url = quote_url
        self.swap_url = swap_url
        self.timeout = timeout

    async def get_swap_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int = 100,  # 1.0% default max slippage
        client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, Any]:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": slippage_bps
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close = True

        try:
            resp = await client.get(self.quote_url, params=params)
            resp.raise_for_status()
            data = resp.json()

            # Enforce Sub-Impact limit on Jupiter quote price impact
            price_impact_pct = float(data.get("priceImpactPct", 0.0)) * 100.0
            if price_impact_pct > 1.5:
                raise ValueError(
                    f"Jupiter Quote Price Impact {price_impact_pct:.2f}% exceeds safe threshold (1.5%). "
                    "Trade rejected to protect treasury."
                )

            return data
        finally:
            if should_close:
                await client.aclose()

    async def build_swap_transaction(
        self,
        quote_response: Dict[str, Any],
        user_pubkey: str,
        client: Optional[httpx.AsyncClient] = None
    ) -> str:
        """
        Builds raw base64 VersionedTransaction using Jupiter V6 API with dynamic compute units.
        """
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto"
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close = True

        try:
            resp = await client.post(self.swap_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["swapTransaction"]
        finally:
            if should_close:
                await client.aclose()

    def sign_jupiter_transaction(
        self,
        swap_tx_base64: str,
        keypair: Keypair
    ) -> VersionedTransaction:
        tx_bytes = base64.b64decode(swap_tx_base64)
        unsigned_tx = VersionedTransaction.from_bytes(tx_bytes)
        # Sign with sub-wallet keypair
        return VersionedTransaction(unsigned_tx.message, [keypair])

    async def plan_floor_defense_ladder(
        self,
        target_token_mint: str,
        dump_size_sol: float,
        defense_wallets: List[Keypair],
        client: Optional[httpx.AsyncClient] = None
    ) -> List[Dict[str, Any]]:
        """
        Floor Defense Mode:
        When external dump > 2.0 SOL occurs, ladder buyback 30-50% across 3 distinct sub-wallets.
        """
        if len(defense_wallets) < 3:
            raise ValueError("Floor Defense requires at least 3 distinct defense sub-wallets")

        total_buyback_sol = dump_size_sol * 0.40  # 40% absorption
        slices = [
            total_buyback_sol * 0.45,  # 1st ladder rung (aggressive)
            total_buyback_sol * 0.35,  # 2nd ladder rung
            total_buyback_sol * 0.20   # 3rd ladder rung
        ]

        ladder_plans = []
        for idx in range(3):
            sol_amount = round(slices[idx], 4)
            wallet = defense_wallets[idx]
            lamports = int(sol_amount * 10**9)

            quote = await self.get_swap_quote(
                input_mint=SOL_MINT,
                output_mint=target_token_mint,
                amount_lamports=lamports,
                slippage_bps=120,
                client=client
            )
            raw_tx = await self.build_swap_transaction(quote, str(wallet.pubkey()), client=client)
            signed_tx = self.sign_jupiter_transaction(raw_tx, wallet)

            ladder_plans.append({
                "rung": idx + 1,
                "wallet_pubkey": str(wallet.pubkey()),
                "amount_sol": sol_amount,
                "signed_transaction": signed_tx,
                "quote": quote
            })

        return ladder_plans
