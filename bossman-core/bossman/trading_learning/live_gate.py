"""Machine-checkable live-readiness gate (the numbers behind
docs/owner/TRADING_LIVE_READINESS_GATE_20261011.md).

This module can only SAY NO or "criteria met on paper". It never enables
anything: TRADING_EXECUTION stays OFF in safety.py and each live action needs a
separate human OwnerApproval. Missing evidence is a failure, not a pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Thresholds are deliberately conservative and written down in the gate doc.
MIN_OOS_TRADES = 100
MIN_INDEPENDENT_PERIODS = 3
MIN_FORWARD_PAPER_DAYS = 14
MAX_DRAWDOWN_FRACTION = 0.20
MAX_POSITION_FRACTION = 0.05
MAX_TOTAL_EXPOSURE_FRACTION = 0.20
MAX_DAILY_LOSS_FRACTION = 0.03
MAX_KILL_SWITCH_SECONDS = 5.0
MAX_SLIPPAGE_DIVERGENCE = 0.25


@dataclass
class Evidence:
    # statistics (from strategy_lab walk-forward, out-of-sample only)
    oos_trades: int = 0
    independent_periods: int = 0
    ci_low_net_return: float | None = None          # 95% bootstrap lower bound
    net_return_at_2x_costs: float | None = None
    ci_low_vs_best_benchmark: float | None = None   # strategy minus max(cash, hold)
    max_drawdown_fraction: float | None = None
    forward_paper_days: int = 0
    slippage_divergence: float | None = None        # |realised-quoted| / quoted
    # operational drills
    kill_switch_drill_passed: bool = False
    kill_switch_seconds: float | None = None
    restart_drill_passed: bool = False
    duplicate_orders_after_restart: int | None = None
    # permissions / limits
    api_key_trade_only: bool = False
    withdrawal_disabled: bool = False
    ip_allowlist: bool = False
    leverage_cap_set: bool = False
    position_fraction: float | None = None
    total_exposure_fraction: float | None = None
    daily_loss_stop_fraction: float | None = None
    monitoring_alerts_live: bool = False
    owner_confirmation_per_action: bool = False
    keys_outside_repo_and_env: bool = False


@dataclass
class GateResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "ALL_CRITERIA_MET_PENDING_OWNER_CONFIRMATION" if not self.failed else "NO_GO"


def _chk(res: GateResult, name: str, ok: bool) -> None:
    (res.passed if ok else res.failed).append(name)


def evaluate_gate(e: Evidence) -> GateResult:
    r = GateResult()
    _chk(r, f"G1 >= {MIN_OOS_TRADES} out-of-sample trades", e.oos_trades >= MIN_OOS_TRADES)
    _chk(r, f"G1 >= {MIN_INDEPENDENT_PERIODS} independent periods",
         e.independent_periods >= MIN_INDEPENDENT_PERIODS)
    _chk(r, "G2 95% CI lower bound of net return > 0",
         e.ci_low_net_return is not None and e.ci_low_net_return > 0)
    _chk(r, "G2 net return still > 0 at 2x costs",
         e.net_return_at_2x_costs is not None and e.net_return_at_2x_costs > 0)
    _chk(r, "G3 beats best benchmark (CI lower bound of difference > 0)",
         e.ci_low_vs_best_benchmark is not None and e.ci_low_vs_best_benchmark > 0)
    _chk(r, f"G4 max drawdown <= {MAX_DRAWDOWN_FRACTION:.0%} of capital",
         e.max_drawdown_fraction is not None and e.max_drawdown_fraction <= MAX_DRAWDOWN_FRACTION)
    _chk(r, f"G5 forward paper >= {MIN_FORWARD_PAPER_DAYS} days",
         e.forward_paper_days >= MIN_FORWARD_PAPER_DAYS)
    _chk(r, f"G5 slippage divergence <= {MAX_SLIPPAGE_DIVERGENCE:.0%}",
         e.slippage_divergence is not None and e.slippage_divergence <= MAX_SLIPPAGE_DIVERGENCE)
    _chk(r, f"G6 kill-switch drill passed and <= {MAX_KILL_SWITCH_SECONDS:g}s",
         e.kill_switch_drill_passed and e.kill_switch_seconds is not None
         and e.kill_switch_seconds <= MAX_KILL_SWITCH_SECONDS)
    _chk(r, "G7 restart drill passed with 0 duplicate orders",
         e.restart_drill_passed and e.duplicate_orders_after_restart == 0)
    _chk(r, "G8 API key trade-only", e.api_key_trade_only)
    _chk(r, "G8 withdrawals disabled", e.withdrawal_disabled)
    _chk(r, "G8 IP allowlist", e.ip_allowlist)
    _chk(r, "G8 leverage cap set (spot / 1x unless owner decides otherwise)", e.leverage_cap_set)
    _chk(r, f"G8 position <= {MAX_POSITION_FRACTION:.0%} of bankroll",
         e.position_fraction is not None and e.position_fraction <= MAX_POSITION_FRACTION)
    _chk(r, f"G8 total exposure <= {MAX_TOTAL_EXPOSURE_FRACTION:.0%} of bankroll",
         e.total_exposure_fraction is not None
         and e.total_exposure_fraction <= MAX_TOTAL_EXPOSURE_FRACTION)
    _chk(r, f"G9 daily loss stop set and <= {MAX_DAILY_LOSS_FRACTION:.0%}",
         e.daily_loss_stop_fraction is not None
         and 0 < e.daily_loss_stop_fraction <= MAX_DAILY_LOSS_FRACTION)
    _chk(r, "G10 monitoring and alerts live", e.monitoring_alerts_live)
    _chk(r, "G11 per-action owner confirmation in place", e.owner_confirmation_per_action)
    _chk(r, "G11 keys outside repo and plaintext .env", e.keys_outside_repo_and_env)
    return r
