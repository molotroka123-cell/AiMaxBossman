"""Read-only constant-product risk assessment. Never authorizes execution.

Protocol-specific on-chain adapters are deliberately unsupported until their
ownership, layout, mint, freshness and valuation checks have been verified.
Caller-supplied reserves are hypothetical inputs, not execution evidence.
"""
from fractions import Fraction
from typing import Dict, Any, Optional, List
import httpx


def fetch_pool_reserves(mint: str, rpc_url: str) -> dict:
    """Fail closed: a mint alone does not identify an authenticated pool."""
    return {"status": "UNKNOWN", "reason": "VERIFIED_POOL_ADAPTER_UNAVAILABLE"}


def check_liquidity(amount_lamports: int, reserves: dict, config: dict) -> dict:
    result = {
        "liquidity_gate_status": "UNKNOWN", "execution_allowed": False,
        "source": "HYPOTHETICAL", "reserve_in": None, "reserve_out": None,
        "pool_liquidity_usd": None, "max_allowed_order_usd": None,
        "estimated_impact_bps": None, "max_allowed_order_lamports": None,
        "reason": "INVALID_OR_MISSING_RESERVES",
    }
    if not isinstance(reserves, dict) or not isinstance(config, dict):
        return result
    try:
        if type(amount_lamports) is not int or amount_lamports <= 0:
            return result
        x, y = reserves["reserve_in"], reserves["reserve_out"]
        if any(type(v) is not int or v <= 0 for v in (x, y)):
            return result
        if reserves.get("model") != "CONSTANT_PRODUCT" or reserves.get("input_asset") != "SOL":
            return result
        limit = config.get("max_impact_bps", 120)
        minimum = config.get("min_reserve_sol", 500)
        share = config.get("max_order_share_bps", 500)
        fee = reserves.get("fee_bps", 25)
        if any(type(v) is not int for v in (limit, minimum, share, fee)):
            return result
        if not (0 < limit <= 120 and minimum >= 500 and 0 < share <= 500 and 0 <= fee < 10000):
            return result
        # Include fee and integer output rounding; round impact upward.
        def impact(amount):
            out = amount * (10000 - fee) * y // (x * 10000 + amount * (10000 - fee))
            loss = max(Fraction(0), 1 - Fraction(out * x, amount * y)) * 10000
            return -(-loss.numerator // loss.denominator)
        estimated = impact(amount_lamports)
        # Necessary upper bound; individual orders still need the exact check.
        bound = max(0, x * (limit - fee) * 10000 // ((10000 - limit) * (10000 - fee)))
        bound = min(bound, x * share // 10000)
        enough = x >= minimum * 10**9
        result.update(reserve_in=x, reserve_out=y, estimated_impact_bps=estimated,
                      max_allowed_order_lamports=bound if enough else 0)
        passed = enough and amount_lamports <= bound and estimated <= limit
        result.update(liquidity_gate_status="PASS" if passed else "BLOCK",
                      reason="HYPOTHETICAL_CHECK_ONLY" if passed else "LIQUIDITY_OR_IMPACT_LIMIT")
        return result
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return result


def split_order_if_needed(amount_lamports: int, reserves: dict, max_impact_bps: int) -> list[int]:
    """Reject an unsafe total instead of disguising it as safe small orders."""
    verdict = check_liquidity(amount_lamports, reserves, {"max_impact_bps": max_impact_bps})
    return [amount_lamports] if verdict["liquidity_gate_status"] == "PASS" else []



class LiquidityGate:
    """Hypothetical assessments cannot authorize transactions or split unsafe orders."""
    def __init__(self, rpc_url=None, max_impact_bps=120):
        self.rpc_url = rpc_url
        self.max_impact_bps = max_impact_bps
        self.last_status = "UNKNOWN"
        self.last_pool_info = {"source": "NOT_FETCHED"}

    async def fetch_dexscreener_reserves(self, mint):
        """Read-only metadata probe; provider values never become verified reserves."""
        from solana_volume_suite.core.security import audit
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=False) as client:
                response = await client.get("https://api.dexscreener.com/latest/dex/tokens/" + mint)
                response.raise_for_status()
        except httpx.TimeoutException:
            audit("warning.rpc_timeout", provider="dexscreener", timeout_seconds=10)
        except httpx.HTTPError:
            audit("warning.rpc_unavailable", provider="dexscreener")
        self.last_status = "UNKNOWN"
        self.last_pool_info = {"source": "UNVERIFIED", "status": "UNKNOWN"}
        return {"status": "UNKNOWN", "reason": "VERIFIED_POOL_ADAPTER_UNAVAILABLE"}

    def validate_and_slice_order(self, amount_sol, pool_reserves=None):
        import math
        valid = (type(amount_sol) in (int, float) and math.isfinite(amount_sol)
                 and 0 < amount_sol <= (2**64 - 1) / 10**9)
        amount = int(amount_sol * 10**9) if valid else 0
        verdict = check_liquidity(amount, pool_reserves or {}, {"max_impact_bps": self.max_impact_bps})
        self.last_status = verdict["liquidity_gate_status"]
        return {"status": self.last_status, "execution_allowed": False,
                "simulation_allowed": self.last_status == "PASS", "original_sol": amount_sol if valid else None,
                "slices_sol": [amount_sol] if self.last_status == "PASS" else [],
                "estimated_impact_bps": verdict["estimated_impact_bps"], "reason": verdict["reason"]}
