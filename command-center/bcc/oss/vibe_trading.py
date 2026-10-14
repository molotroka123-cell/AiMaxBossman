"""Vibe-Trading (`vibe-trading` v0.1.15, MIT license) Shadow Account integration for Bossman.

Wires Vibe-Trading's personal trading agent capabilities strictly in Shadow Account
(`PAPER_ONLY`, `real_money=0`, `SIMULATED=True`) mode and integrates with Fincept
regime detection (`S4_regime_trend`) and Bossman's Solana Paper Arena rules.

Any attempt to enable live trading or pass non-zero real money is hard-blocked (`T3 deny`,
Eternal Prohibition #1).
"""
from __future__ import annotations

from typing import Any

from . import fincept_terminal as fincept

PACKAGE_NAME = "vibe-trading"
UPSTREAM_VERSION = "0.1.15"
LICENSE = "MIT"
UPSTREAM_URL = "https://github.com/HKUDS/Vibe-Trading"
ALLOWED_MODES = frozenset({"shadow", "shadow_account", "paper", "paper_only", "simulation"})


def status() -> dict[str, Any]:
    """Side-effect-free inventory status for Vibe-Trading Shadow Account."""
    return {
        "id": "vibe_trading",
        "package": PACKAGE_NAME,
        "version_declared": UPSTREAM_VERSION,
        "license": LICENSE,
        "status": "integrated",
        "mode": "shadow_account_only",
        "paper_only": True,
        "real_money": 0,
        "live_trading_blocked": True,
        "live_gate": "NO_GO",
        "inference_verified": False,
        "upstream": UPSTREAM_URL,
    }


def enforce_shadow_account(mode: str = "shadow", *, real_money: float = 0.0, live_api_key: str = "") -> dict[str, Any]:
    """Enforce Eternal Prohibition #1: Vibe-Trading may only run in Shadow Account (`PAPER_ONLY`) mode."""
    norm_mode = (mode or "shadow").strip().lower()
    try:
        money_val = float(real_money or 0.0)
    except (TypeError, ValueError):
        money_val = 0.0

    if norm_mode not in ALLOWED_MODES or money_val > 0.0 or bool((live_api_key or "").strip()):
        reason = (
            "Вечный запрет #1 (T3 DENY): Vibe-Trading в Bossman работает ТОЛЬКО в режиме "
            "Shadow Account (PAPER_ONLY, real_money=0). Живые ордера, реальные деньги и биржевые ключи вывода запрещены."
        )
        return {
            "allowed": False,
            "tier": "T3",
            "decision": "deny",
            "mode": "shadow_account_only",
            "requested_mode": norm_mode,
            "real_money": 0,
            "reason": reason,
        }
    return {
        "allowed": True,
        "tier": "T0",
        "decision": "auto",
        "mode": "shadow_account_only",
        "requested_mode": norm_mode,
        "real_money": 0,
        "reason": "OK: Shadow Account (PAPER_ONLY, real_money=0, SIMULATED=True).",
    }


def simulate_shadow_step(
    symbol: str,
    closes: list[float],
    *,
    equity_usd: float = 100.0,
    fee_bps_rt: float = 28.0,
    daily_stop_pct: float = 3.0,
    mode: str = "shadow",
    real_money: float = 0.0,
    live_api_key: str = "",
) -> dict[str, Any]:
    """Run one deterministic Shadow Account evaluation step with `S4_regime_trend` discipline."""
    guard = enforce_shadow_account(mode, real_money=real_money, live_api_key=live_api_key)
    if not guard["allowed"]:
        raise PermissionError(guard["reason"])

    analytics = fincept.analyze_series(symbol, closes, fee_bps_rt=fee_bps_rt)
    start_eq = round(max(1.0, float(equity_usd or 100.0)), 2)
    regime = analytics["regime"]
    fee_rate = max(0.0, float(fee_bps_rt)) / 10_000.0

    if regime == "CHOP" or analytics["fee_drag_warning"]:
        # S4_regime_trend lesson: stay flat in CHOP so 28 bps RT fees do not bleed equity
        position_side = "FLAT"
        orders_placed = 0
        gross_pnl_usd = 0.0
        fee_paid_usd = 0.0
        net_pnl_usd = 0.0
        end_eq = start_eq
        halted_by_daily_stop = False
        action_taken = "HOLD_CHOP_FILTER"
    else:
        position_side = "LONG_SHADOW" if regime == "TREND_UP" else "SHORT_SHADOW"
        orders_placed = 1
        direction = 1.0 if regime == "TREND_UP" else -1.0
        raw_ret = ((analytics["last_close"] - analytics["first_close"]) / analytics["first_close"]) * direction
        # Cap single-step loss by daily stop limit (-3%)
        stop_frac = -abs(float(daily_stop_pct)) / 100.0
        capped_ret = max(stop_frac, raw_ret)
        gross_pnl_usd = round(start_eq * capped_ret, 4)
        fee_paid_usd = round(start_eq * fee_rate, 4)
        net_pnl_usd = round(gross_pnl_usd - fee_paid_usd, 4)
        end_eq = round(start_eq + net_pnl_usd, 2)
        halted_by_daily_stop = ( (net_pnl_usd / start_eq) * 100.0 ) <= -abs(float(daily_stop_pct))
        action_taken = "SHADOW_PAPER_FILL"

    return {
        "ok": True,
        "SIMULATED": True,
        "PAPER_ONLY": True,
        "shadow_account": True,
        "real_money": 0,
        "live_gate": "NO_GO",
        "package": PACKAGE_NAME,
        "version_declared": UPSTREAM_VERSION,
        "license": LICENSE,
        "strategy": "S4_regime_trend",
        "symbol": analytics["symbol"],
        "regime": regime,
        "efficiency_ratio": analytics["efficiency_ratio"],
        "action_taken": action_taken,
        "position_side": position_side,
        "orders_placed": orders_placed,
        "equity_start_usd": start_eq,
        "gross_pnl_usd": gross_pnl_usd,
        "fee_paid_usd": fee_paid_usd,
        "net_pnl_usd": net_pnl_usd,
        "equity_end_usd": end_eq,
        "halted_by_daily_stop": halted_by_daily_stop,
        "analytics": analytics,
        "summary_ru": (
            f"Vibe-Trading Shadow Account (`{analytics['symbol']}`, `PAPER_ONLY=True`, `real_money=0`): "
            f"режим `{regime}` (ER={analytics['efficiency_ratio']:.2f}) → `{action_taken}`, "
            f"позиция `{position_side}`, эквити ${start_eq:.2f} → ${end_eq:.2f} "
            f"(комиссия: ${fee_paid_usd:.2f}, Live Gate: `NO_GO`)."
        ),
    }
