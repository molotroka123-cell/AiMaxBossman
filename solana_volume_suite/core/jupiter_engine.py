import base64
import httpx
from typing import Dict, Any, Optional
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair

DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"


class JupiterSwapEngine:
    """
    Jupiter V6 Engine for post-migration Raydium / Meteora / Orca swap routing.
    Includes Price Impact Limiter and Dynamic Compute Unit optimization.
    """

    def __init__(self, rpc_url: str = DEFAULT_RPC_URL):
        self.quote_url = "https://quote-api.jup.ag/v6/quote"
        self.swap_url = "https://quote-api.jup.ag/v6/swap"
        self.rpc_url = rpc_url

    async def get_swap_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int = 150,
        max_price_impact_bps: Optional[int] = 120
    ) -> Dict[str, Any]:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": slippage_bps,
            "onlyDirectRoutes": False,
            "asLegacyTransaction": False
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(self.quote_url, params=params)
            quote = resp.json()
            if "priceImpactPct" in quote and max_price_impact_bps is not None:
                impact_bps = int(float(quote["priceImpactPct"]) * 10000)
                if impact_bps > max_price_impact_bps:
                    raise Exception(f"Price impact {impact_bps} bps exceeds limit {max_price_impact_bps} bps.")
            return quote

    async def build_swap_transaction(
        self,
        quote_response: Dict[str, Any],
        user_pubkey: str,
        prioritization_fee_lamports: int = 5000
    ) -> str:
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": prioritization_fee_lamports,
            "asLegacyTransaction": False
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(self.swap_url, json=payload)
            return resp.json()["swapTransaction"]

    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        user_keypair: Keypair,
        slippage_bps: int = 150
    ) -> str:
        from solana_volume_suite.core.security import audit
        audit("SECURITY_VIOLATION", reason="SWAP_SIGNING_DISABLED")
        raise PermissionError("VIRTUAL_ONLY: swap signing and execution are disabled")
