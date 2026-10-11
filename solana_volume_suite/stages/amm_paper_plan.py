"""Stage 2: post-migration AMM (Raydium/Jupiter-class) PAPER planner.

Replaces the removed Jupiter v6 quote/swap/sign helpers (audit H4). It computes a constant-product estimate over
reserves SUPPLIED BY THE CALLER, applies the slippage cap and the impact gate, and returns a frozen plan. No quote
API, no swap-transaction building, no signing, no network.
"""
from __future__ import annotations

from typing import Optional

from core.paper_plan import (
    DEFAULT_SLIPPAGE_BPS,
    MAX_QUOTED_IMPACT_PCT,
    PaperAmmSwapPlan,
    PlanRejected,
    check_quoted_impact,
    min_output_lamports,
)
from core.price_impact_calculator import PriceImpactCalculator

SOL_MINT = "So11111111111111111111111111111111111111112"


def plan_amm_swap(
    input_mint: str,
    output_mint: str,
    amount_in_lamports: int,
    reserve_in: int,
    reserve_out: int,
    fee_bps: int = 25,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    max_impact_pct: Optional[float] = MAX_QUOTED_IMPACT_PCT,
) -> PaperAmmSwapPlan:
    for name, value in (("amount_in_lamports", amount_in_lamports), ("reserve_in", reserve_in),
                        ("reserve_out", reserve_out)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PlanRejected(f"{name} must be a positive integer")
    if isinstance(fee_bps, bool) or not isinstance(fee_bps, int) or not 0 <= fee_bps < 10_000:
        raise PlanRejected("fee_bps must be an integer in [0, 10000)")
    with_fee = amount_in_lamports * (10_000 - fee_bps)
    est_out = with_fee * reserve_out // (reserve_in * 10_000 + with_fee)
    impact_bps = PriceImpactCalculator.calculate_price_impact_bps(amount_in_lamports, reserve_in, reserve_out, fee_bps)
    impact_pct = impact_bps / 100.0
    if max_impact_pct is not None:
        check_quoted_impact(impact_pct, max_impact_pct)
    return PaperAmmSwapPlan(
        venue="amm_constant_product", input_mint=input_mint, output_mint=output_mint,
        amount_in_lamports=amount_in_lamports, est_out=est_out,
        min_out=min_output_lamports(est_out, slippage_bps), slippage_bps=slippage_bps, impact_pct=impact_pct,
    )
