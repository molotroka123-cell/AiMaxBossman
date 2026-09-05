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
        from solana_volume_suite.core.security import audit
        audit("SECURITY_VIOLATION", reason="LEGACY_SWAP_SIGNING_DISABLED")
        raise PermissionError("VIRTUAL_ONLY: signing disabled")

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
        from solana_volume_suite.core.security import audit
        audit("SECURITY_VIOLATION", reason="LIVE_STRATEGY_DISABLED")
        raise PermissionError("VIRTUAL_ONLY: live strategy planning disabled")
