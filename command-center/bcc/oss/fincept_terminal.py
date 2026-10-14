"""Fincept Terminal (`FinceptTerminal`, AGPL-3.0 open-source edition) integration for Bossman.

Provides deterministic, read-only market analytics, investment research, and economic
regime/risk metrics (`CHOP` vs `TREND`, Kaufman Efficiency Ratio, Sharpe, Sortino,
max drawdown, and RT fee drag).

Strictly enforces Bossman Eternal Prohibition #1:
  - Only the free AGPL-3.0 open-source edition is allowed (`cost_usd=0.0`).
  - Paid Enterprise subscriptions (`Exclusive $10/mo`, `Exclusive+ $20/mo`,
    `Exclusive Pro $40/mo`) and `Live broker & algo execution` are hard-blocked (`T3 deny`).
"""
from __future__ import annotations

import math
from typing import Any

PACKAGE_NAME = "FinceptTerminal"
LICENSE = "AGPL-3.0"
UPSTREAM_URL = "https://github.com/Fincept-Corporation/FinceptTerminal"
ALLOWED_EDITION = "open_source_agpl3"
BLOCKED_ENTERPRISE_PLANS = frozenset({
    "enterprise", "exclusive", "exclusive+", "exclusive_plus",
    "exclusive_pro", "exclusive pro", "claim_launch_price", "paid",
})
BLOCKED_LIVE_ACTIONS = frozenset({
    "live_broker", "live_broker_execution", "algo_execution",
    "place_live_order", "claim_launch_price", "subscribe_enterprise",
    "pay_subscription",
})


def status() -> dict[str, Any]:
    """Side-effect-free inventory status for Fincept Terminal (AGPL-3.0 open-source edition)."""
    return {
        "id": "fincept_terminal",
        "package": PACKAGE_NAME,
        "license": LICENSE,
        "edition": ALLOWED_EDITION,
        "status": "integrated",
        "mode": "read_only_research",
        "cost_usd": 0.0,
        "paid_enterprise_blocked": True,
        "live_broker_execution_blocked": True,
        "inference_verified": False,
        "upstream": UPSTREAM_URL,
    }


def check_fincept_policy(action: str = "research", *, edition: str = ALLOWED_EDITION) -> dict[str, Any]:
    """Enforce Eternal Prohibition #1 on Fincept Terminal usage.

    Any attempt to purchase Enterprise plans ($10/$20/$40/mo) or trigger live broker
    execution is denied at Tier T3.
    """
    act = (action or "research").strip().lower()
    ed = (edition or ALLOWED_EDITION).strip().lower()
    if ed in BLOCKED_ENTERPRISE_PLANS or act in BLOCKED_LIVE_ACTIONS or "enterprise" in ed or "live_broker" in act:
        reason = (
            "Вечный запрет #1 (T3 DENY): платные подписки Fincept Terminal Enterprise "
            "($10/$20/$40/мес) и живое брокерское исполнение (Live broker & algo execution) "
            "запрещены. Разрешена только бесплатная AGPL-3.0 open-source аналитика (read_only_research, $0)."
        )
        return {
            "allowed": False,
            "tier": "T3",
            "decision": "deny",
            "edition": ALLOWED_EDITION,
            "requested_edition": ed,
            "requested_action": act,
            "cost_usd": 0.0,
            "reason": reason,
        }
    return {
        "allowed": True,
        "tier": "T0",
        "decision": "auto",
        "edition": ALLOWED_EDITION,
        "requested_action": act,
        "cost_usd": 0.0,
        "reason": "OK: бесплатный AGPL-3.0 контур аналитики и исследований (только чтение, $0).",
    }


def analyze_series(
    symbol: str,
    closes: list[float],
    *,
    fee_bps_rt: float = 28.0,
    edition: str = ALLOWED_EDITION,
    action: str = "research",
) -> dict[str, Any]:
    """Deterministic market analytics & regime classification (Fincept research desk)."""
    gate = check_fincept_policy(action, edition=edition)
    if not gate["allowed"]:
        raise PermissionError(gate["reason"])

    clean_symbol = (symbol or "SOL/USDT").strip().upper()
    pts = [float(x) for x in (closes or []) if isinstance(x, (int, float)) and math.isfinite(float(x)) and float(x) > 0]
    if len(pts) < 3:
        raise ValueError("closes: требуется минимум 3 положительные цены закрытия для расчёта метрик")

    rets = [(pts[i] - pts[i - 1]) / pts[i - 1] for i in range(1, len(pts))]
    n = len(rets)
    mean_ret = sum(rets) / n
    variance = sum((r - mean_ret) ** 2 for r in rets) / max(1, n - 1) if n > 1 else 0.0
    std_ret = math.sqrt(max(0.0, variance))
    downside_sq = [min(0.0, r) ** 2 for r in rets]
    downside_dev = math.sqrt(sum(downside_sq) / n)

    sharpe = round((mean_ret / std_ret) * math.sqrt(252.0), 3) if std_ret > 1e-12 else 0.0
    sortino = round((mean_ret / downside_dev) * math.sqrt(252.0), 3) if downside_dev > 1e-12 else 0.0

    # Max drawdown
    peak = pts[0]
    max_dd_pct = 0.0
    for p in pts:
        if p > peak:
            peak = p
        dd = ((peak - p) / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Kaufman Efficiency Ratio (ER) for regime classification (S4_regime_trend)
    net_change = abs(pts[-1] - pts[0])
    path_length = sum(abs(pts[i] - pts[i - 1]) for i in range(1, len(pts)))
    efficiency_ratio = round(net_change / path_length, 4) if path_length > 1e-12 else 0.0
    total_return_pct = round(((pts[-1] - pts[0]) / pts[0]) * 100.0, 3)

    if efficiency_ratio < 0.35:
        regime = "CHOP"
    elif pts[-1] >= pts[0]:
        regime = "TREND_UP"
    else:
        regime = "TREND_DOWN"

    rt_fee_pct = round(max(0.0, float(fee_bps_rt)) / 100.0, 4)
    avg_abs_move_pct = round((sum(abs(r) for r in rets) / n) * 100.0, 4)
    fee_drag_warning = regime == "CHOP" or avg_abs_move_pct <= rt_fee_pct * 1.5

    return {
        "ok": True,
        "package": PACKAGE_NAME,
        "license": LICENSE,
        "edition": ALLOWED_EDITION,
        "mode": "read_only_research",
        "real_money": 0,
        "cost_usd": 0.0,
        "symbol": clean_symbol,
        "samples": len(pts),
        "first_close": round(pts[0], 4),
        "last_close": round(pts[-1], 4),
        "total_return_pct": total_return_pct,
        "efficiency_ratio": efficiency_ratio,
        "regime": regime,
        "sharpe_annualized": sharpe,
        "sortino_annualized": sortino,
        "max_drawdown_pct": round(max_dd_pct, 3),
        "fee_bps_rt": float(fee_bps_rt),
        "avg_abs_step_pct": avg_abs_move_pct,
        "fee_drag_warning": fee_drag_warning,
        "recommendation": (
            "HOLD_FILTER_CHOP: боковик (CHOP) или ход меньше комиссии 28 bps RT — входы заблокированы фильтром S4_regime_trend."
            if fee_drag_warning
            else f"PAPER_SIGNAL_{regime}: направленное движение (ER={efficiency_ratio:.2f}) превышает комиссионный порог, допустим только тест на бумажном счёте."
        ),
    }
