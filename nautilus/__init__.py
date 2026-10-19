"""NAUTILUS — Read-only / Shadow-Account Strategy Analytics & Telegram Pult Report Generator.

Strictly adheres to Bossman Eternal Prohibition #1:
- NO real money transfers
- NO live broker / exchange order execution
- NO private keys, seed phrases, or wallet credentials
- Pure read-only portfolio analytics, multi-window metric computation, 6-tile dark dashboard card
  rendering, rebalance trade-ticket generation, stress-testing, and owner-only Telegram Pult reporting.
"""
from __future__ import annotations

from .card import render_nautilus_card, save_nautilus_card
from .metrics import (
    MultiWindowReport,
    PerformanceMetrics,
    WindowSummary,
    compute_beta,
    compute_cagr,
    compute_calmar,
    compute_dollar_becomes,
    compute_max_drawdown,
    compute_multi_window_report,
    compute_performance_metrics,
    compute_sharpe,
    compute_sortino,
    compute_volatility,
    compute_winning_weeks_streak,
)
from .portfolio import (
    MarketRegimeBlock,
    LeverageExposureBlock,
    TradeTicketItem,
    PortfolioComparisonBlock,
    StressAuditBlock,
    NautilusOperationalState,
    build_operational_state,
    generate_trade_ticket,
    run_stress_audit,
)
from .telegram_report import (
    format_nautilus_telegram_message,
    load_sample_fixture,
    send_nautilus_report_to_pult,
)

__all__ = [
    "LeverageExposureBlock",
    "MarketRegimeBlock",
    "MultiWindowReport",
    "NautilusOperationalState",
    "PerformanceMetrics",
    "PortfolioComparisonBlock",
    "StressAuditBlock",
    "TradeTicketItem",
    "WindowSummary",
    "build_operational_state",
    "compute_beta",
    "compute_cagr",
    "compute_calmar",
    "compute_dollar_becomes",
    "compute_max_drawdown",
    "compute_multi_window_report",
    "compute_performance_metrics",
    "compute_sharpe",
    "compute_sortino",
    "compute_volatility",
    "compute_winning_weeks_streak",
    "format_nautilus_telegram_message",
    "generate_trade_ticket",
    "load_sample_fixture",
    "render_nautilus_card",
    "run_stress_audit",
    "save_nautilus_card",
    "send_nautilus_report_to_pult",
]
