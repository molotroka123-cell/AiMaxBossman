"""Multi-window performance metrics calculator for NAUTILUS & NAUTILUS_BTC vs SPY / BTC.

Implements:
- 6 core dashboard metrics: CAGR, SHARPE, SORTINO, MAX DRAWDOWN, CALMAR, $1 BECOMES
- Multi-window analytics:
  1. YTD (YTD Return, Sharpe, Max DD vs BTC / SPY, plus exposure delta e.g. 30% -> 100% long BTC)
  2. Since Inception (NAUTILUS Return, SPY Return, Excess Return, Sharpe, Volatility, Max DD, Beta)
  3. Last 26 trading sessions (Sharpe vs SPY, Volatility vs SPY, Max DD vs SPY, Beta)
  4. Winning weeks streak (e.g. 13+ weeks in a row)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class PerformanceMetrics:
    """Core 6-tile performance metrics + volatility and beta."""
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    calmar: float
    dollar_becomes: float
    volatility_pct: float = 0.0
    beta: float = 0.0


@dataclass(frozen=True)
class WindowSummary:
    """Comparison summary for a specific evaluation window (YTD, Since Inception, Last 26 Sessions)."""
    window_name: str
    strategy_name: str
    benchmark_name: str
    strategy_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    strategy_sharpe: float
    benchmark_sharpe: float
    strategy_sortino: float
    benchmark_sortino: float
    strategy_max_dd_pct: float
    benchmark_max_dd_pct: float
    strategy_calmar: float
    benchmark_calmar: float
    strategy_dollar_becomes: float
    benchmark_dollar_becomes: float
    strategy_volatility_pct: float
    benchmark_volatility_pct: float
    beta: float
    exposure_delta_note: str = ""


@dataclass(frozen=True)
class MultiWindowReport:
    """Aggregated multi-window performance report for NAUTILUS."""
    card_strategy: PerformanceMetrics
    card_benchmark: PerformanceMetrics
    card_strategy_label: str
    card_benchmark_label: str
    ytd_btc: WindowSummary
    since_inception: WindowSummary
    last_26_sessions: WindowSummary
    winning_weeks_streak: int
    winning_weeks_label: str
    metadata: dict[str, Any] = field(default_factory=dict)


def compute_dollar_becomes(returns: Sequence[float]) -> float:
    """Compound sequence of period fractional returns (e.g. 0.01 for +1%) starting from $1.00."""
    if not returns:
        return 1.0
    equity = 1.0
    for r in returns:
        if not math.isfinite(r):
            raise ValueError("Return series contains non-finite number")
        if r <= -1.0:
            return 0.0
        equity *= 1.0 + float(r)
    return equity


def compute_cagr(returns: Sequence[float], periods_per_year: float = 252.0) -> float:
    """Compute annualized CAGR (in percent, e.g. 42.19 for 42.19%) from fractional period returns."""
    if not returns or periods_per_year <= 0:
        return 0.0
    final_eq = compute_dollar_becomes(returns)
    if final_eq <= 0.0:
        return -100.0
    years = len(returns) / float(periods_per_year)
    if years <= 0:
        return 0.0
    return ((final_eq ** (1.0 / years)) - 1.0) * 100.0


def compute_volatility(returns: Sequence[float], periods_per_year: float = 252.0) -> float:
    """Compute annualized sample volatility in percent."""
    n = len(returns)
    if n < 2 or periods_per_year <= 0:
        return 0.0
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    return math.sqrt(max(0.0, var) * periods_per_year) * 100.0


def compute_sharpe(
    returns: Sequence[float],
    risk_free_annual: float = 0.0,
    periods_per_year: float = 252.0,
) -> float:
    """Compute annualized Sharpe ratio from fractional period returns."""
    n = len(returns)
    if n < 2 or periods_per_year <= 0:
        return 0.0
    rf_period = risk_free_annual / float(periods_per_year)
    excess = [float(r) - rf_period for r in returns]
    mean_ex = sum(excess) / n
    var = sum((x - mean_ex) ** 2 for x in excess) / (n - 1)
    std = math.sqrt(max(0.0, var))
    if std <= 1e-12:
        return 0.0
    return (mean_ex / std) * math.sqrt(periods_per_year)


def compute_sortino(
    returns: Sequence[float],
    mar_annual: float = 0.0,
    periods_per_year: float = 252.0,
) -> float:
    """Compute annualized Sortino ratio using downside deviation below MAR."""
    n = len(returns)
    if n < 2 or periods_per_year <= 0:
        return 0.0
    mar_period = mar_annual / float(periods_per_year)
    excess = [float(r) - mar_period for r in returns]
    mean_ex = sum(excess) / n
    downside_sq = [min(0.0, x) ** 2 for x in excess]
    downside_var = sum(downside_sq) / n
    downside_dev = math.sqrt(max(0.0, downside_var))
    if downside_dev <= 1e-12:
        return 0.0 if mean_ex <= 0 else 99.99
    return (mean_ex / downside_dev) * math.sqrt(periods_per_year)


def compute_max_drawdown(returns: Sequence[float]) -> float:
    """Compute maximum drawdown in percent (negative or zero, e.g. -23.62 for -23.62%)."""
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= max(0.0, 1.0 + float(r))
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (equity / peak) - 1.0
            if dd < max_dd:
                max_dd = dd
    return max_dd * 100.0


def compute_calmar(cagr_pct: float, max_drawdown_pct: float) -> float:
    """Compute Calmar ratio = CAGR / |Max Drawdown|."""
    denom = abs(float(max_drawdown_pct))
    if denom <= 1e-9:
        return 0.0
    return float(cagr_pct) / denom


def compute_beta(strategy_returns: Sequence[float], benchmark_returns: Sequence[float]) -> float:
    """Compute portfolio Beta relative to benchmark returns."""
    n = min(len(strategy_returns), len(benchmark_returns))
    if n < 2:
        return 0.0
    s = [float(x) for x in strategy_returns[:n]]
    b = [float(x) for x in benchmark_returns[:n]]
    mean_s = sum(s) / n
    mean_b = sum(b) / n
    cov = sum((s[i] - mean_s) * (b[i] - mean_b) for i in range(n)) / (n - 1)
    var_b = sum((b[i] - mean_b) ** 2 for i in range(n)) / (n - 1)
    if var_b <= 1e-15:
        return 0.0
    return cov / var_b


def compute_winning_weeks_streak(weekly_returns: Sequence[float]) -> int:
    """Count consecutive non-negative (> 0) winning weeks at the tail of the weekly return series."""
    if not weekly_returns:
        return 0
    streak = 0
    for r in reversed(weekly_returns):
        if float(r) > 0.0:
            streak += 1
        else:
            break
    return streak


def compute_performance_metrics(
    returns: Sequence[float],
    benchmark_returns: Sequence[float] | None = None,
    periods_per_year: float = 252.0,
    risk_free_annual: float = 0.0,
) -> PerformanceMetrics:
    """Compute full PerformanceMetrics from a return series."""
    dollar = compute_dollar_becomes(returns)
    total_ret_pct = (dollar - 1.0) * 100.0
    cagr = compute_cagr(returns, periods_per_year=periods_per_year)
    sharpe = compute_sharpe(returns, risk_free_annual=risk_free_annual, periods_per_year=periods_per_year)
    sortino = compute_sortino(returns, mar_annual=risk_free_annual, periods_per_year=periods_per_year)
    max_dd = compute_max_drawdown(returns)
    calmar = compute_calmar(cagr, max_dd)
    vol = compute_volatility(returns, periods_per_year=periods_per_year)
    beta = compute_beta(returns, benchmark_returns) if benchmark_returns is not None else 0.0
    return PerformanceMetrics(
        total_return_pct=round(total_ret_pct, 2),
        cagr_pct=round(cagr, 2),
        sharpe=round(sharpe, 2),
        sortino=round(sortino, 2),
        max_drawdown_pct=round(max_dd, 2),
        calmar=round(calmar, 2),
        dollar_becomes=round(dollar, 2),
        volatility_pct=round(vol, 2),
        beta=round(beta, 3),
    )


def _metrics_from_dict_or_series(
    block: dict[str, Any],
    prefix: str,
    periods_per_year: float = 252.0,
) -> PerformanceMetrics:
    """Build PerformanceMetrics either from explicit snapshot values or from raw returns series."""
    returns = block.get(f"{prefix}_returns")
    bench_returns = block.get("benchmark_returns")
    computed = (
        compute_performance_metrics(returns, bench_returns, periods_per_year=periods_per_year)
        if returns
        else None
    )

    def pick(key: str, default: float = 0.0) -> float:
        full_key = f"{prefix}_{key}" if prefix else key
        if full_key in block and block[full_key] is not None:
            return float(block[full_key])
        if computed is not None:
            return float(getattr(computed, key))
        return default

    total_ret = pick("total_return_pct", 0.0)
    cagr = pick("cagr_pct", total_ret)
    max_dd = pick("max_drawdown_pct", 0.0)
    calmar_val = block.get(f"{prefix}_calmar" if prefix else "calmar")
    if calmar_val is None:
        calmar_val = compute_calmar(cagr, max_dd)
    dollar_val = block.get(f"{prefix}_dollar_becomes" if prefix else "dollar_becomes")
    if dollar_val is None:
        dollar_val = round(1.0 + total_ret / 100.0, 2)

    return PerformanceMetrics(
        total_return_pct=round(total_ret, 2),
        cagr_pct=round(cagr, 2),
        sharpe=round(pick("sharpe", 0.0), 2),
        sortino=round(pick("sortino", 0.0), 2),
        max_drawdown_pct=round(max_dd, 2),
        calmar=round(float(calmar_val), 2),
        dollar_becomes=round(float(dollar_val), 2),
        volatility_pct=round(pick("volatility_pct", 0.0), 2),
        beta=round(float(block.get("beta", computed.beta if computed else 0.0)), 3),
    )


def build_window_summary(window_name: str, data: dict[str, Any]) -> WindowSummary:
    """Construct a WindowSummary from fixture/computed dictionary."""
    strat = _metrics_from_dict_or_series(data, "strategy")
    bench = _metrics_from_dict_or_series(data, "benchmark")
    excess = data.get("excess_return_pct")
    if excess is None:
        excess = round(strat.total_return_pct - bench.total_return_pct, 2)
    return WindowSummary(
        window_name=window_name,
        strategy_name=str(data.get("strategy_name", "NAUTILUS")),
        benchmark_name=str(data.get("benchmark_name", "SPY")),
        strategy_return_pct=strat.total_return_pct,
        benchmark_return_pct=bench.total_return_pct,
        excess_return_pct=round(float(excess), 2),
        strategy_sharpe=strat.sharpe,
        benchmark_sharpe=bench.sharpe,
        strategy_sortino=strat.sortino,
        benchmark_sortino=bench.sortino,
        strategy_max_dd_pct=strat.max_drawdown_pct,
        benchmark_max_dd_pct=bench.max_drawdown_pct,
        strategy_calmar=strat.calmar,
        benchmark_calmar=bench.calmar,
        strategy_dollar_becomes=strat.dollar_becomes,
        benchmark_dollar_becomes=bench.dollar_becomes,
        strategy_volatility_pct=strat.volatility_pct,
        benchmark_volatility_pct=bench.volatility_pct,
        beta=strat.beta,
        exposure_delta_note=str(data.get("exposure_delta_note", "")),
    )


def compute_multi_window_report(payload: dict[str, Any]) -> MultiWindowReport:
    """Compute or assemble the full MultiWindowReport from a NAUTILUS data payload."""
    card_data = payload.get("dashboard_card") or {}
    card_strat = _metrics_from_dict_or_series(card_data, "strategy")
    card_bench = _metrics_from_dict_or_series(card_data, "benchmark")

    ytd_summary = build_window_summary("YTD", payload.get("ytd_btc") or {})
    inception_summary = build_window_summary("Since Inception", payload.get("since_inception") or {})
    last26_summary = build_window_summary("Last 26 Trading Sessions", payload.get("last_26_sessions") or {})

    weekly_returns = payload.get("weekly_returns") or []
    computed_streak = compute_winning_weeks_streak(weekly_returns)
    streak = int(payload.get("winning_weeks_streak", computed_streak))
    streak_label = str(payload.get("winning_weeks_label") or f"{streak}+ weeks in a row")

    return MultiWindowReport(
        card_strategy=card_strat,
        card_benchmark=card_bench,
        card_strategy_label=str(card_data.get("strategy_label", "NAUTILUS")),
        card_benchmark_label=str(card_data.get("benchmark_label", "SPY")),
        ytd_btc=ytd_summary,
        since_inception=inception_summary,
        last_26_sessions=last26_summary,
        winning_weeks_streak=streak,
        winning_weeks_label=streak_label,
        metadata=dict(payload.get("metadata") or {}),
    )
