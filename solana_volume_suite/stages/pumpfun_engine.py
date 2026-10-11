"""Stage 1: Pump.fun bonding-curve PAPER planner.

Owner decision 2026-10-11 (audit H4): the hand-built buy/sell instructions, bundle assembly and Jito tip/transaction
compilation were removed. This module only turns inputs into immutable paper plans (`core.paper_plan`). It imports
no Solana SDK, takes no keypair, builds no instruction/transaction and makes no network request.
"""
from __future__ import annotations

import math
from typing import Optional

from core.paper_plan import (
    DEFAULT_SLIPPAGE_BPS,
    LAMPORTS_PER_SOL,
    MAX_IMPACT_PCT,
    MIGRATION_CUTOFF_PCT,
    PUMP_INITIAL_VIRTUAL_SOL,
    PUMP_INITIAL_VIRTUAL_TOKENS,
    MigrationThresholdExceededException,
    PaperBuyPlan,
    PaperSellPlan,
    PaperSlice,
    PlanRejected,
    RentReclaimPlanItem,
    check_quoted_impact,
    curve_sol_out,
    curve_tokens_out,
    estimate_price_impact_pct,
    estimate_tip_lamports,
    max_input_lamports,
    min_output_lamports,
    slice_order,
    validate_curve_safety,
)

__all__ = [
    "MIGRATION_CUTOFF_PCT",
    "MigrationThresholdExceededException",
    "PumpFunPaperPlanner",
]


class PumpFunPaperPlanner:
    """
    Paper planner for the Pump.fun virtual bonding curve.
    - 95% migration cutoff for BUY plans
    - sub-impact slicing (<= 1.2% impact per slice)
    - integer-lamport slippage caps
    - rent reclaim as a plan item on a full exit
    """

    def validate_curve_safety(self, curve_progress_pct: float, action: str) -> None:
        validate_curve_safety(curve_progress_pct, action)

    def plan_buy(
        self,
        mint: str,
        amount_sol: float,
        curve_progress_pct: float,
        virtual_sol_reserve: float = PUMP_INITIAL_VIRTUAL_SOL,
        virtual_token_reserve: float = PUMP_INITIAL_VIRTUAL_TOKENS,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
        tip_tier: str = "medium",
    ) -> PaperBuyPlan:
        self.validate_curve_safety(curve_progress_pct, "BUY")
        if not isinstance(mint, str) or not mint:
            raise PlanRejected("mint must be a non-empty string")
        if not math.isfinite(virtual_token_reserve) or virtual_token_reserve <= 0:
            raise PlanRejected("virtual_token_reserve must be finite and > 0")
        sizes = slice_order(amount_sol, virtual_sol_reserve, max_impact_pct=MAX_IMPACT_PCT)
        sol_res, tok_res = float(virtual_sol_reserve), float(virtual_token_reserve)
        slices = []
        total_tokens = 0.0
        for idx, size in enumerate(sizes):
            impact = estimate_price_impact_pct(size, sol_res)
            tokens = curve_tokens_out(size, sol_res, tok_res)
            slices.append(PaperSlice(
                index=idx, sol=size, impact_pct=round(impact, 4), est_tokens=round(tokens, 2),
                cap_lamports=max_input_lamports(int(round(size * LAMPORTS_PER_SOL)), slippage_bps),
            ))
            total_tokens += tokens
            sol_res += size          # the next paper slice sees the curve after this one
            tok_res -= tokens
        return PaperBuyPlan(
            venue="pumpfun_bonding_curve", mint=mint, requested_sol=float(amount_sol),
            curve_progress_pct=float(curve_progress_pct), slippage_bps=slippage_bps,
            slices=tuple(slices), total_est_tokens=round(total_tokens, 2),
            tip_estimate_lamports=estimate_tip_lamports(tip_tier),
        )

    def plan_sell(
        self,
        mint: str,
        token_amount: float,
        virtual_sol_reserve: float = PUMP_INITIAL_VIRTUAL_SOL,
        virtual_token_reserve: float = PUMP_INITIAL_VIRTUAL_TOKENS,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
        reclaim_rent: bool = True,
        tip_tier: str = "medium",
        max_impact_pct: Optional[float] = None,
    ) -> PaperSellPlan:
        if not isinstance(mint, str) or not mint:
            raise PlanRejected("mint must be a non-empty string")
        for name, value in (("token_amount", token_amount), ("virtual_sol_reserve", virtual_sol_reserve),
                            ("virtual_token_reserve", virtual_token_reserve)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise PlanRejected(f"{name} must be a finite number > 0")
        expected_sol = curve_sol_out(token_amount, virtual_sol_reserve, virtual_token_reserve)
        impact = estimate_price_impact_pct(expected_sol, virtual_sol_reserve)
        if max_impact_pct is not None:
            check_quoted_impact(impact, max_impact_pct)
        expected_lamports = int(round(expected_sol * LAMPORTS_PER_SOL))
        return PaperSellPlan(
            venue="pumpfun_bonding_curve", mint=mint, token_amount=float(token_amount),
            slippage_bps=slippage_bps, expected_sol=expected_lamports / LAMPORTS_PER_SOL,
            min_sol_lamports=min_output_lamports(expected_lamports, slippage_bps),
            impact_pct=round(impact, 4), rent_reclaim=RentReclaimPlanItem(included=bool(reclaim_rent)),
            tip_estimate_lamports=estimate_tip_lamports(tip_tier),
        )
