"""Paper strategy lab: costs, walk-forward, bootstrap CI, negative control."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bossman.trading_learning.metrics import CostModel, evaluate
from bossman.trading_learning.models import Candle, Decision, MarketRegime, TradeResult
from bossman.trading_learning.strategy_lab import (
    Candidate, ExitRule, LabError, RealisticCosts, bootstrap_mean_ci,
    mean_reversion_signal, momentum_signal, random_signal, simulate_trades,
    synthetic_random_walk, trade_returns, walk_forward)


def _flat(n=40):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        p = 100.0
        out.append(Candle(ts=t0 + timedelta(minutes=5 * (i + 1)), open=p, high=p * 1.001,
                          low=p * 0.999, close=p, volume=1.0))
    return out


def always_long(_h):
    return Decision.LONG


def test_costs_charged_on_both_legs_exactly_once():
    c = RealisticCosts(fee_rate=0.001, half_spread_rate=0.0005, slippage_rate=0.0005,
                       funding_rate_per_bar=0.0, partial_fill_probability=0.0)
    trades = simulate_trades(_flat(), always_long, ExitRule(0.05, 0.05, 3), c, warmup=2)
    t = trades[0]
    assert t.gross_pnl == pytest.approx(0.0, abs=1e-9)
    # fee 0.1% + spread 0.05% + slippage 0.05% per leg, two legs, notional 100
    assert t.net_pnl == pytest.approx(-100.0 * 2 * 0.002, rel=1e-6)
    assert t.net_pnl == pytest.approx(-t.fees - t.slippage - t.funding)


def test_no_double_counting_ev_equals_mean_net_pnl():
    trades = [TradeResult(f"e{i}", Decision.LONG, 100.0, 100.0 + d, 99.0, 1.0,
                          fees=0.2, funding=0.1, slippage=0.3,
                          regime=MarketRegime.RANGE if i % 2 else MarketRegime.CONTINUATION,
                          out_of_sample=i % 3 == 0)
              for i, d in enumerate([2, -1, 3, -1, 1, 4] * 6)]
    rep = evaluate(trades, costs=CostModel(execution_error_rate=0.0))
    mean_net = sum(t.net_pnl for t in trades) / len(trades)
    assert rep.expected_value == pytest.approx(mean_net)
    oos = [t for t in trades if t.out_of_sample]
    assert rep.out_of_sample_ev == pytest.approx(sum(t.net_pnl for t in oos) / len(oos))
    assert rep.cost_sensitivity["gross_ev"] == pytest.approx(
        sum(t.gross_pnl for t in trades) / len(trades))


def test_stop_gap_fills_at_open_not_at_stop():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [Candle(t0 + timedelta(minutes=5 * (i + 1)), 100, 100.2, 99.8, 100, 1)
            for i in range(25)]
    bars[22] = Candle(bars[22].ts, 95.0, 95.5, 94.0, 94.5, 1)
    c = RealisticCosts(partial_fill_probability=0.0)
    trades = simulate_trades(bars, always_long, ExitRule(0.01, 0.5, 10), c, warmup=2)
    assert any(t.exit == 95.0 for t in trades)
    assert not any(94.5 < t.exit < 99.0 and t.exit != 95.0 for t in trades)


def test_partial_fills_scale_size_and_are_deterministic():
    c = RealisticCosts(partial_fill_probability=1.0, min_fill_fraction=0.5)
    candles = synthetic_random_walk(300, seed=3)
    a = simulate_trades(candles, always_long, ExitRule(0.01, 0.01, 3), c, seed=9)
    b = simulate_trades(candles, always_long, ExitRule(0.01, 0.01, 3), c, seed=9)
    assert [t.size for t in a] == [t.size for t in b]
    assert all(0.5 <= t.size <= 1.0 for t in a) and any(t.size < 1.0 for t in a)


def test_zero_latency_rejected():
    with pytest.raises(LabError):
        RealisticCosts(latency_bars=0)


def test_bootstrap_ci_wide_on_few_samples_and_includes_zero():
    ci = bootstrap_mean_ci([0.01, -0.02, 0.015, -0.01, 0.005], seed=1)
    assert ci.includes_zero and ci.n == 5
    strong = bootstrap_mean_ci([0.01 + 0.001 * (i % 3) for i in range(80)], seed=1)
    assert strong.strictly_positive and not strong.includes_zero


def test_bootstrap_is_seeded_reproducible():
    v = [0.01 * ((i * 7) % 5 - 2) for i in range(60)]
    assert bootstrap_mean_ci(v, seed=5) == bootstrap_mean_ci(v, seed=5)


def _candidates():
    out = [Candidate(f"mom{lb}_{th}", momentum_signal(lb, th), ExitRule(0.01, 0.02, 10))
           for lb, th in ((10, 0.01), (20, 0.015))]
    out.append(Candidate("mr20_2", mean_reversion_signal(20, 2.0), ExitRule(0.01, 0.01, 8)))
    return out


def test_negative_control_random_strategy_never_looks_profitable():
    """A coin-flip strategy on a driftless random walk must not pass, across seeds."""
    costs = RealisticCosts()
    false_edges = 0
    means = []
    for seed in range(12):
        candles = synthetic_random_walk(2500, seed=100 + seed)
        cands = [Candidate(f"rnd{k}", random_signal(0.15, seed=seed * 10 + k),
                           ExitRule(0.01, 0.01, 8)) for k in range(4)]
        rep = walk_forward(candles, cands, costs, train_bars=500, test_bars=250,
                           seed=seed, resamples=400)
        means.append(rep.net_mean_return)
        if rep.verdict == "EDGE_CANDIDATE_PAPER_ONLY":
            false_edges += 1
    assert false_edges == 0
    assert sum(means) / len(means) < 0.0


def test_report_states_plainly_when_ci_includes_zero():
    candles = synthetic_random_walk(1500, seed=42)
    rep = walk_forward(candles, _candidates(), RealisticCosts(), train_bars=400,
                       test_bars=200, resamples=300)
    assert rep.verdict in {"NO_EDGE_PROVEN", "LOSES_MONEY_AFTER_COSTS", "INSUFFICIENT_EVIDENCE"}
    if rep.ci.includes_zero:
        assert "INCLUDES ZERO" in rep.text and "no statistical evidence" in rep.text
    assert "paper only" in rep.text.lower()


def test_positive_control_trending_series_is_detected():
    candles = synthetic_random_walk(6000, seed=11, autocorr=0.55, vol=0.004)
    cands = [Candidate("mom5", momentum_signal(5, 0.004), ExitRule(0.02, 0.04, 8)),
             Candidate("mom10", momentum_signal(10, 0.008), ExitRule(0.02, 0.04, 8))]
    rep = walk_forward(candles, cands, RealisticCosts(), train_bars=800, test_bars=400,
                       resamples=500)
    assert rep.gross_mean_return > 0
    assert rep.ci.strictly_positive, rep.text
    assert rep.verdict == "EDGE_CANDIDATE_PAPER_ONLY", rep.text


def test_walk_forward_test_windows_never_overlap_training():
    candles = synthetic_random_walk(1500, seed=2)
    rep = walk_forward(candles, _candidates(), RealisticCosts(), train_bars=400,
                       test_bars=200, embargo_bars=5, resamples=100)
    assert len(rep.folds) >= 3
    for f in rep.folds:
        assert f.train_range[1] + 5 == f.test_range[0]
    assert all(t.out_of_sample for t in rep.oos_trades)


def test_trade_returns_are_cost_inclusive():
    trades = simulate_trades(_flat(), always_long, ExitRule(0.05, 0.05, 3),
                             RealisticCosts(partial_fill_probability=0.0), warmup=2)
    assert all(r < 0 for r in trade_returns(trades))
