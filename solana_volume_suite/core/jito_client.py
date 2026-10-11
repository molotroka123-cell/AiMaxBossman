"""Inert Jito stub of the paper-only Solana volume suite.

Owner decision 2026-10-11 (audit H4): tip-account lists, tip/transaction compilation (`compile_v0_transaction`,
`build_tip_instruction`) and bundle-status polling were removed. What remains is a tip ESTIMATE (a number used by
the paper cost model) and `send_bundle`, which always refuses. No Solana SDK object, keypair or network client is
imported here.
"""
import random
from typing import Any, Dict, List, Optional

from core.paper_plan import TIP_TIERS_LAMPORTS

DEFAULT_JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"  # label only; never requested
MIN_JITO_TIP_LAMPORTS = 100_000  # 0.0001 SOL floor
MAX_BUNDLE_TRANSACTIONS = 5


class JitoBundleDropException(Exception):
    """Raised when a bundle is refused. PUBLIC MEMPOOL FALLBACK IS FORBIDDEN."""
    pass


class JitoBundleClient:
    """
    Paper-only stand-in. Keeps the tip estimate and the counters shown on the dashboard.
    MEMPOOL INVARIANT: nothing is ever broadcast; every bundle is dropped.
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

    def calculate_dynamic_tip(self, network_congestion: Any = "medium") -> int:
        """
        Estimates a tip for the paper cost model.
        Supports:
          - String tiers: "low" (10k), "medium" (50k), "high" (100k), "extreme" (250k)
          - Numeric multiplier: float/int
        """
        if isinstance(network_congestion, str) and network_congestion.lower() in TIP_TIERS_LAMPORTS:
            return TIP_TIERS_LAMPORTS[network_congestion.lower()]

        try:
            mult = max(1.0, float(network_congestion))
        except (ValueError, TypeError):
            mult = 1.0

        base = int(self.min_tip_lamports * mult)
        jitter = int(base * random.uniform(0.01, 0.15))
        return base + jitter

    async def send_bundle(
        self,
        transactions: List[Any],
        client: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Always refuses. STRICT FAIL-CLOSED INVARIANT: the bundle is DROPPED, never routed to standard RPC.
        """
        self.total_bundles_dropped += 1
        raise JitoBundleDropException(
            "LIVE_EXECUTION_DISABLED: Bundle DROPPED (no public mempool fallback)."
        )
