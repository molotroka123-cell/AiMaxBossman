"""Paper strategy lab: walk-forward evaluation with realistic costs and bootstrap CIs.

Purpose: answer ONE question honestly - "does this rule have a measurable edge
after costs, out of sample?" - and say plainly when the answer is "no evidence".

Design rules (each one answers a known way to fool yourself):
  * Costs on BOTH legs: fee, half-spread and slippage are charged at entry AND
    exit; funding per bar held; fills may be partial; execution has latency.
  * Net P&L is booked once. ``TradeResult.entry/exit`` hold REFERENCE prices
    (bar open / stop / take / close) and every cost sits in the cost fields, so
    ``net_pnl = gross - costs`` can never charge slippage twice (audit finding
    7: the older ``evaluate`` subtracted costs from an already-net mean).
  * Parameters are chosen only on the training window; the test window is
    never seen by the selection (walk-forward, no overlap, embargo optional).
  * Verdict comes from a bootstrap confidence interval of the mean net return
    per trade on OUT-OF-SAMPLE trades only. If the interval includes zero the
    report says so in plain words and the verdict is NO_EDGE_PROVEN.
  * Nothing here talks to a network, an exchange or a key. Paper only.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

from .metrics import max_drawdown
from .models import Candle, Decision, MarketRegime, TradeResult
from .safety import assert_no_live_execution

Signal = Callable[[Sequence[Candle]], "Decision | None"]


class LabError(ValueError):
    pass


# --------------------------------------------------------------------- costs
@dataclass(frozen=True, slots=True)
class RealisticCosts:
    """All rates are fractions of notional. Every leg pays them."""

    fee_rate: float = 0.0004          # taker fee, per leg
    half_spread_rate: float = 0.0003  # half of bid-ask spread, per leg
    slippage_rate: float = 0.0005     # extra adverse move, per leg
    funding_rate_per_bar: float = 0.00001
    latency_bars: int = 1             # signal on bar i, fill on bar i+latency
    min_fill_fraction: float = 0.6    # partial fills: fraction drawn in [min, 1]
    partial_fill_probability: float = 0.3

    def __post_init__(self) -> None:
        if self.latency_bars < 1:
            raise LabError("latency_bars must be >= 1 (no instant execution)")
        if not 0.0 < self.min_fill_fraction <= 1.0:
            raise LabError("min_fill_fraction must be in (0,1]")
        for name in ("fee_rate", "half_spread_rate", "slippage_rate", "funding_rate_per_bar"):
            if getattr(self, name) < 0:
                raise LabError(f"{name} must be non-negative")

    def per_leg_rate(self) -> float:
        return self.fee_rate + self.half_spread_rate + self.slippage_rate

    def round_trip_rate(self) -> float:
        return 2.0 * self.per_leg_rate()

    def scaled(self, k: float) -> "RealisticCosts":
        return RealisticCosts(self.fee_rate * k, self.half_spread_rate * k,
                              self.slippage_rate * k, self.funding_rate_per_bar * k,
                              self.latency_bars, self.min_fill_fraction,
                              self.partial_fill_probability)


@dataclass(frozen=True, slots=True)
class ExitRule:
    stop_pct: float = 0.01
    take_pct: float = 0.02
    max_hold_bars: int = 12

    def __post_init__(self) -> None:
        if self.stop_pct <= 0 or self.take_pct <= 0 or self.max_hold_bars <= 0:
            raise LabError("stop, take and max_hold must be positive")


# ---------------------------------------------------------- trade simulation
def _exit(direction: Decision, entry_ref: float, bars: Sequence[Candle],
          rule: ExitRule) -> tuple[int, float]:
    """First of stop / take / time. Stop is checked BEFORE take inside a bar and
    a gap through the stop fills at the bar open (worse), never at the stop."""
    long = direction is Decision.LONG
    stop = entry_ref * (1 - rule.stop_pct) if long else entry_ref * (1 + rule.stop_pct)
    take = entry_ref * (1 + rule.take_pct) if long else entry_ref * (1 - rule.take_pct)
    window = bars[:rule.max_hold_bars]
    for i, bar in enumerate(window):
        gapped = bar.open <= stop if long else bar.open >= stop
        if gapped:
            return i, bar.open
        if (bar.low <= stop) if long else (bar.high >= stop):
            return i, stop
        if (bar.high >= take) if long else (bar.low <= take):
            gap_take = bar.open >= take if long else bar.open <= take
            return i, (bar.open if gap_take else take)
    return len(window) - 1, window[-1].close


def simulate_trades(candles: Sequence[Candle], signal: Signal, exit_rule: ExitRule,
                    costs: RealisticCosts, *, warmup: int = 20, seed: int = 0,
                    out_of_sample: bool = False, regime: MarketRegime = MarketRegime.UNKNOWN,
                    ) -> list[TradeResult]:
    """One position at a time. Signal sees candles[:i+1] only (no look-ahead)."""
    assert_no_live_execution("lab_simulate", stage="paper_trading")
    rng = random.Random(seed)
    trades: list[TradeResult] = []
    i = max(warmup, 1)
    n = len(candles)
    while i < n - costs.latency_bars - 1:
        direction = signal(candles[:i + 1])
        if direction not in (Decision.LONG, Decision.SHORT):
            i += 1
            continue
        entry_idx = i + costs.latency_bars
        entry_ref = candles[entry_idx].open
        tail = candles[entry_idx:]
        # the entry bar itself may already trade through the stop: start at it
        j, exit_ref = _exit(direction, entry_ref, tail, exit_rule)
        hold = j + 1
        size = 1.0
        if rng.random() < costs.partial_fill_probability:
            size = rng.uniform(costs.min_fill_fraction, 1.0)
        notional = entry_ref * size
        fees = notional * costs.fee_rate + exit_ref * size * costs.fee_rate
        spread_slip = (notional * (costs.half_spread_rate + costs.slippage_rate)
                       + exit_ref * size * (costs.half_spread_rate + costs.slippage_rate))
        funding = notional * costs.funding_rate_per_bar * hold
        stop_ref = (entry_ref * (1 - exit_rule.stop_pct) if direction is Decision.LONG
                    else entry_ref * (1 + exit_rule.stop_pct))
        trades.append(TradeResult(
            episode_id=f"lab:{entry_idx}", direction=direction, entry=entry_ref,
            exit=exit_ref, stop=stop_ref, size=size, fees=fees, funding=funding,
            slippage=spread_slip, regime=regime, out_of_sample=out_of_sample))
        i = entry_idx + hold  # flat again after the exit bar
    return trades


def trade_returns(trades: Sequence[TradeResult]) -> list[float]:
    """Net return per trade as a fraction of entry notional (cost-inclusive)."""
    out = []
    for t in trades:
        notional = t.entry * t.size
        out.append(t.net_pnl / notional if notional > 0 else 0.0)
    return out


# ---------------------------------------------------------------- bootstrap
@dataclass(frozen=True, slots=True)
class BootstrapCI:
    mean: float
    low: float
    high: float
    n: int
    level: float
    resamples: int
    block: int

    @property
    def includes_zero(self) -> bool:
        return self.low <= 0.0 <= self.high

    @property
    def strictly_positive(self) -> bool:
        return self.low > 0.0


def bootstrap_mean_ci(values: Sequence[float], *, level: float = 0.95,
                      resamples: int = 2000, seed: int = 12345,
                      block: int | None = None) -> BootstrapCI:
    """Percentile CI of the mean with a moving-block bootstrap.

    Trades are not guaranteed independent (overlapping regimes), so blocks of
    length ~n^(1/3) keep serial dependence. Few samples -> a WIDE interval,
    which is the honest answer.
    """
    n = len(values)
    if n == 0:
        return BootstrapCI(0.0, 0.0, 0.0, 0, level, resamples, 1)
    b = block or max(1, round(n ** (1 / 3)))
    b = min(b, n)
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(0, n - b + 1)
            sample.extend(values[start:start + b])
        sample = sample[:n]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1 - level) / 2
    lo = means[max(0, int(math.floor(alpha * resamples)))]
    hi = means[min(resamples - 1, int(math.ceil((1 - alpha) * resamples)) - 1)]
    return BootstrapCI(sum(values) / n, lo, hi, n, level, resamples, b)


# -------------------------------------------------------------- walk-forward
@dataclass(frozen=True, slots=True)
class Candidate:
    """One parameter set under test: a named signal plus its exit rule."""

    name: str
    signal: Signal
    exit_rule: ExitRule


@dataclass
class FoldResult:
    fold: int
    train_range: tuple[int, int]
    test_range: tuple[int, int]
    chosen: str
    train_mean_return: float
    test_trades: int
    test_mean_return: float


@dataclass
class LabReport:
    folds: list[FoldResult] = field(default_factory=list)
    oos_trades: list[TradeResult] = field(default_factory=list)
    oos_returns: list[float] = field(default_factory=list)
    ci: BootstrapCI | None = None
    gross_mean_return: float = 0.0
    net_mean_return: float = 0.0
    max_drawdown: float = 0.0
    candidates_tried: int = 0
    cost_stress: dict[str, float] = field(default_factory=dict)
    verdict: str = "INSUFFICIENT_EVIDENCE"
    blockers: tuple[str, ...] = ()
    text: str = ""


MIN_OOS_TRADES = 30


def walk_forward(candles: Sequence[Candle], candidates: Sequence[Candidate],
                 costs: RealisticCosts, *, train_bars: int, test_bars: int,
                 embargo_bars: int = 0, seed: int = 7,
                 resamples: int = 2000) -> LabReport:
    """Rolling walk-forward: select on train, trade the NEXT test window.

    Only test-window trades enter the verdict. The chosen candidate is picked by
    mean net return on the training slice; there is no peeking at the test slice.
    """
    assert_no_live_execution("lab_walk_forward", stage="paper_trading")
    if not candidates:
        raise LabError("no candidates")
    if train_bars < 50 or test_bars < 20:
        raise LabError("windows too small to say anything")
    rep = LabReport(candidates_tried=len(candidates))
    start = 0
    fold = 0
    while start + train_bars + embargo_bars + test_bars <= len(candles):
        tr = candles[start:start + train_bars]
        te_lo = start + train_bars + embargo_bars
        # test slice carries `warmup` bars of history so signals are defined at
        # its first bar, but trades are only opened inside the test range.
        warm = 20
        te = candles[te_lo - warm:te_lo + test_bars]
        scored = []
        for c in candidates:
            trs = simulate_trades(tr, c.signal, c.exit_rule, costs, seed=seed + fold)
            rets = trade_returns(trs)
            scored.append((sum(rets) / len(rets) if len(rets) >= 5 else -1.0, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        trs = simulate_trades(te, best.signal, best.exit_rule, costs,
                              warmup=warm, seed=seed + fold, out_of_sample=True)
        rets = trade_returns(trs)
        rep.folds.append(FoldResult(fold, (start, start + train_bars),
                                    (te_lo, te_lo + test_bars), best.name, best_score,
                                    len(trs), sum(rets) / len(rets) if rets else 0.0))
        rep.oos_trades.extend(trs)
        start += test_bars
        fold += 1

    rep.oos_returns = trade_returns(rep.oos_trades)
    n = len(rep.oos_returns)
    rep.ci = bootstrap_mean_ci(rep.oos_returns, resamples=resamples, seed=seed)
    rep.net_mean_return = rep.ci.mean
    if rep.oos_trades:
        gross = [t.gross_pnl / (t.entry * t.size) for t in rep.oos_trades]
        rep.gross_mean_return = sum(gross) / len(gross)
    rep.max_drawdown = max_drawdown(rep.oos_trades)
    rep.cost_stress = {
        f"x{k}": _mean_net_with_cost_scale(rep.oos_trades, costs, k) for k in (0.0, 1.0, 2.0, 3.0)}

    blockers: list[str] = []
    if n < MIN_OOS_TRADES:
        blockers.append(f"only {n} out-of-sample trades (< {MIN_OOS_TRADES})")
    if rep.ci.includes_zero or rep.ci.high < 0:
        blockers.append("bootstrap CI of net return includes zero" if rep.ci.includes_zero
                        else "bootstrap CI of net return is entirely negative")
    if len(rep.folds) < 3:
        blockers.append(f"only {len(rep.folds)} walk-forward folds (< 3)")
    if rep.cost_stress.get("x2.0", 0.0) <= 0:
        blockers.append("edge disappears at 2x costs")
    rep.blockers = tuple(blockers)
    if n == 0:
        rep.verdict = "INSUFFICIENT_EVIDENCE"
    elif not blockers:
        rep.verdict = "EDGE_CANDIDATE_PAPER_ONLY"
    elif rep.ci.high < 0:
        rep.verdict = "LOSES_MONEY_AFTER_COSTS"
    else:
        rep.verdict = "NO_EDGE_PROVEN"
    rep.text = render_report(rep)
    return rep


def _mean_net_with_cost_scale(trades: Sequence[TradeResult], base: RealisticCosts,
                              k: float) -> float:
    """Re-price already simulated trades at k times the cost model (gross is fixed)."""
    if not trades:
        return 0.0
    out = []
    for t in trades:
        notional = t.entry * t.size
        if notional <= 0:
            continue
        legs = base.per_leg_rate() * (notional + t.exit * t.size) * k
        hold_funding = t.funding * k
        out.append((t.gross_pnl - legs - hold_funding) / notional)
    return sum(out) / len(out) if out else 0.0


def render_report(rep: LabReport) -> str:
    ci = rep.ci
    lines = ["PAPER STRATEGY LAB REPORT (paper only - not a trading recommendation)"]
    lines.append(f"candidates tried: {rep.candidates_tried}; folds: {len(rep.folds)}; "
                 f"out-of-sample trades: {len(rep.oos_returns)}")
    if ci and ci.n:
        lines.append(f"net mean return per trade (OOS, after all costs): {ci.mean:+.4%}")
        lines.append(f"{int(ci.level * 100)}% bootstrap CI: [{ci.low:+.4%}, {ci.high:+.4%}] "
                     f"({ci.resamples} resamples, block={ci.block})")
        lines.append(f"gross mean return before costs: {rep.gross_mean_return:+.4%}")
        lines.append("cost stress (mean net return at cost multiples): " + ", ".join(
            f"{k}={v:+.4%}" for k, v in rep.cost_stress.items()))
        lines.append(f"max drawdown (quote units, size=1): {rep.max_drawdown:.4f}")
        if ci.includes_zero:
            lines.append("RESULT: the confidence interval INCLUDES ZERO. There is no "
                         "statistical evidence of an edge after costs.")
        elif ci.high < 0:
            lines.append("RESULT: the confidence interval is entirely BELOW zero. "
                         "The rule loses money after costs.")
        else:
            lines.append("RESULT: the confidence interval is above zero on paper. This is "
                         "necessary but NOT sufficient for live trading (see readiness gate).")
    else:
        lines.append("RESULT: no out-of-sample trades; nothing can be concluded.")
    lines.append(f"verdict: {rep.verdict}")
    if rep.blockers:
        lines.append("blockers: " + "; ".join(rep.blockers))
    lines.append(f"note: {rep.candidates_tried} candidates were searched per fold; selection "
                 "is done on training data only, but multiple-testing risk remains.")
    return "\n".join(lines)


# ------------------------------------------------------- reference signals
def momentum_signal(lookback: int, threshold: float) -> Signal:
    def sig(history: Sequence[Candle]):
        if len(history) <= lookback:
            return None
        ret = history[-1].close / history[-1 - lookback].close - 1
        if ret > threshold:
            return Decision.LONG
        if ret < -threshold:
            return Decision.SHORT
        return None
    return sig


def mean_reversion_signal(lookback: int, z: float) -> Signal:
    def sig(history: Sequence[Candle]):
        if len(history) < lookback:
            return None
        closes = [c.close for c in history[-lookback:]]
        m = sum(closes) / lookback
        sd = math.sqrt(sum((x - m) ** 2 for x in closes) / lookback)
        if sd <= 0:
            return None
        score = (closes[-1] - m) / sd
        if score > z:
            return Decision.SHORT
        if score < -z:
            return Decision.LONG
        return None
    return sig


def random_signal(probability: float, seed: int) -> Signal:
    """Negative control: coin-flip entries. Must never look profitable."""
    rng = random.Random(seed)

    def sig(history: Sequence[Candle]):
        if rng.random() < probability:
            return Decision.LONG if rng.random() < 0.5 else Decision.SHORT
        return None
    return sig


def synthetic_random_walk(n: int, *, seed: int, start: float = 100.0,
                          vol: float = 0.006, drift: float = 0.0,
                          autocorr: float = 0.0) -> list[Candle]:
    """Deterministic synthetic OHLC series for controls and fixtures."""
    from datetime import timedelta, timezone
    rng = random.Random(seed)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = start
    prev = 0.0
    out: list[Candle] = []
    for i in range(n):
        shock = rng.gauss(0.0, vol)
        r = drift + autocorr * prev + shock
        prev = r
        o = price
        c = max(0.01, o * (1 + r))
        wick = abs(rng.gauss(0.0, vol / 2))
        hi = max(o, c) * (1 + wick)
        lo = min(o, c) * (1 - wick)
        out.append(Candle(ts=t0 + timedelta(minutes=5 * (i + 1)), open=o, high=hi,
                          low=lo, close=c, volume=1000.0))
        price = c
    return out
