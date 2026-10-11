"""Pure paper-plan maths for the Solana volume suite.

Owner decision 2026-10-11: the suite keeps only PAPER PLANS. This module is deliberately free of `solders`, `spl`,
`httpx` and any signing/network primitive: a plan is a frozen dataclass of plain numbers and strings that can be
inspected, logged and tested. Nothing here can be turned into a transaction, and no function takes a keypair.

Covered (all deterministic, integer lamports where money is involved):
  * curve safety check (95% migration cutoff)
  * sub-impact slicing (delegates to `SubImpactEngine`)
  * price-impact estimate for a bonding curve / constant-product pool
  * slippage cap maths (max input / min output)
  * rent reclaim expressed as a plan item (no instruction object)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Dict, Tuple

from core.treasury_guard import SubImpactEngine

LAMPORTS_PER_SOL = 10**9
MIGRATION_CUTOFF_PCT = 95.0
MAX_PLAN_SOL = 100.0          # same ceiling as VolumeDecision / LiquidityGate
MAX_PLAN_SLICES = 100
MAX_SLIPPAGE_BPS = 500        # 5% hard cap for any paper plan
DEFAULT_SLIPPAGE_BPS = 500
MAX_IMPACT_PCT = 1.2          # per-slice price-impact ceiling
MAX_QUOTED_IMPACT_PCT = 1.5   # ceiling for an externally supplied quote's impact figure
TOKEN_ACCOUNT_RENT_LAMPORTS = 2_039_280   # 0.00203928 SOL, rent-exempt minimum of a 165-byte token account
PUMP_INITIAL_VIRTUAL_SOL = 30.0
PUMP_INITIAL_VIRTUAL_TOKENS = 1_073_000_000.0
TIP_TIERS_LAMPORTS: Dict[str, int] = {"low": 10_000, "medium": 50_000, "high": 100_000, "extreme": 250_000}


class MigrationThresholdExceededException(Exception):
    """Bonding curve is at or above the migration cutoff: a paper BUY plan is refused."""


class PlanRejected(ValueError):
    """A paper plan input is out of bounds (non-finite, non-positive, too large, too many slices)."""


# --------------------------------------------------------------------------------------------- validation
def _finite_positive(value: Any, name: str, maximum: float = MAX_PLAN_SOL) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlanRejected(f"{name} must be a finite number")
    if value <= 0:
        raise PlanRejected(f"{name} must be > 0")
    if value > maximum:
        raise PlanRejected(f"{name} must be <= {maximum}")
    return float(value)


def _slippage(bps: Any) -> int:
    if isinstance(bps, bool) or not isinstance(bps, int) or not 0 <= bps <= MAX_SLIPPAGE_BPS:
        raise PlanRejected(f"slippage_bps must be an integer in [0, {MAX_SLIPPAGE_BPS}]")
    return bps


# --------------------------------------------------------------------------------------------- maths
def validate_curve_safety(curve_progress_pct: float, action: str) -> None:
    """Refuse a BUY plan at or above the migration cutoff (capital would be locked during migration)."""
    if isinstance(curve_progress_pct, bool) or not isinstance(curve_progress_pct, (int, float)) \
            or not math.isfinite(curve_progress_pct):
        raise PlanRejected("curve_progress_pct must be a finite number")
    if str(action).upper() == "BUY" and curve_progress_pct >= MIGRATION_CUTOFF_PCT:
        raise MigrationThresholdExceededException(
            f"Curve progress is {curve_progress_pct:.2f}% >= {MIGRATION_CUTOFF_PCT}%. "
            "Buying blocked to prevent capital lock during migration to Raydium."
        )


def estimate_price_impact_pct(order_sol: float, pool_sol_reserve: float) -> float:
    """impact% = order / (reserve + order) * 100 (constant-product approximation)."""
    return SubImpactEngine.calculate_price_impact(order_sol, pool_sol_reserve)


def slice_order(
    requested_sol: float, pool_sol_reserve: float, max_impact_pct: float = MAX_IMPACT_PCT
) -> Tuple[float, ...]:
    amount = _finite_positive(requested_sol, "requested_sol")
    _finite_positive(pool_sol_reserve, "pool_sol_reserve", maximum=1e12)
    slices = SubImpactEngine.enforce_sub_impact_limits(amount, pool_sol_reserve, max_impact_pct=max_impact_pct)
    if len(slices) > MAX_PLAN_SLICES:
        raise PlanRejected(f"plan needs {len(slices)} slices > {MAX_PLAN_SLICES}: pool too shallow for this order")
    return tuple(slices)


def max_input_lamports(amount_lamports: int, slippage_bps: int) -> int:
    """Cap on what a BUY may spend: ceil(amount * (1 + bps/10000))."""
    bps = _slippage(slippage_bps)
    return -(-int(amount_lamports) * (10_000 + bps) // 10_000)


def min_output_lamports(expected_out: int, slippage_bps: int) -> int:
    """Floor on what a SELL must return: floor(expected * (1 - bps/10000))."""
    bps = _slippage(slippage_bps)
    return int(expected_out) * (10_000 - bps) // 10_000


def curve_tokens_out(sol_in: float, virtual_sol: float, virtual_tokens: float) -> float:
    """Constant-product token output for `sol_in` against virtual reserves (estimate, not a quote)."""
    return virtual_tokens * sol_in / (virtual_sol + sol_in)


def curve_sol_out(tokens_in: float, virtual_sol: float, virtual_tokens: float) -> float:
    return virtual_sol * tokens_in / (virtual_tokens + tokens_in)


def check_quoted_impact(price_impact_pct: float, limit_pct: float = MAX_QUOTED_IMPACT_PCT) -> float:
    """Gate for an impact figure supplied by the CALLER (this module never fetches quotes)."""
    if isinstance(price_impact_pct, bool) or not isinstance(price_impact_pct, (int, float)) \
            or not math.isfinite(price_impact_pct) or price_impact_pct < 0:
        raise PlanRejected("price_impact_pct must be a finite non-negative number")
    if price_impact_pct > limit_pct:
        raise PlanRejected(f"price impact {price_impact_pct:.2f}% exceeds safe threshold ({limit_pct}%)")
    return float(price_impact_pct)


def estimate_tip_lamports(tier: str = "medium") -> int:
    if tier not in TIP_TIERS_LAMPORTS:
        raise PlanRejected(f"unknown tip tier {tier!r}")
    return TIP_TIERS_LAMPORTS[tier]


# --------------------------------------------------------------------------------------------- plan objects
class _Plan:
    """Base: every plan is paper-only and says so."""
    live_executable: ClassVar[bool] = False

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)  # type: ignore[call-overload]
        out["live_executable"] = False
        out["plan_kind"] = type(self).__name__
        return out


@dataclass(frozen=True)
class PaperSlice(_Plan):
    index: int
    sol: float
    impact_pct: float
    est_tokens: float              # BUY: tokens received; SELL: tokens sold
    cap_lamports: int              # BUY: max lamports to spend; SELL: min lamports to receive


@dataclass(frozen=True)
class RentReclaimPlanItem(_Plan):
    """Closing an empty token account returns its rent. A PLAN ITEM only: no instruction is built."""
    kind: str = "close_empty_token_account"
    estimated_lamports: int = TOKEN_ACCOUNT_RENT_LAMPORTS
    included: bool = True
    condition: str = "only after the full position is sold and the token balance is zero"


@dataclass(frozen=True)
class PaperBuyPlan(_Plan):
    venue: str
    mint: str
    requested_sol: float
    curve_progress_pct: float
    slippage_bps: int
    slices: Tuple[PaperSlice, ...]
    total_est_tokens: float
    tip_estimate_lamports: int
    notes: str = "PAPER_PLAN_ONLY: no instruction, transaction, signature or request is produced"


@dataclass(frozen=True)
class PaperSellPlan(_Plan):
    venue: str
    mint: str
    token_amount: float
    slippage_bps: int
    expected_sol: float
    min_sol_lamports: int
    impact_pct: float
    rent_reclaim: RentReclaimPlanItem
    tip_estimate_lamports: int
    notes: str = "PAPER_PLAN_ONLY: no instruction, transaction, signature or request is produced"


@dataclass(frozen=True)
class PaperAmmSwapPlan(_Plan):
    """Post-migration swap plan over caller-supplied reserves (no quote API, no route building)."""
    venue: str
    input_mint: str
    output_mint: str
    amount_in_lamports: int
    est_out: int
    min_out: int
    slippage_bps: int
    impact_pct: float
    notes: str = "PAPER_PLAN_ONLY: no instruction, transaction, signature or request is produced"
