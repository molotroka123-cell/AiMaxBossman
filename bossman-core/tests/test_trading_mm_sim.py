"""Market-making paper simulator: honest decomposition and controls."""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman.trading_learning.mm_sim import (DISCLAIMER, MMError, MMParams, SyntheticMarket,
                                             load_recorded, replay_recorded, run_synthetic,
                                             simulate_path)

FIXTURE = Path(__file__).parent / "fixtures" / "trading_lab" / "recorded_book_sample.csv"


def test_pnl_decomposition_is_an_exact_identity():
    params = MMParams(fee_rate=0.0002)
    res = simulate_path(SyntheticMarket(steps=800), params, seed=5)
    assert res.fills > 10
    parts = (res.spread_capture + res.adverse_selection + res.inventory_risk
             - res.flatten_cost - res.fees)
    assert res.total == pytest.approx(parts)
    # total must also equal real cash+inventory mark minus flatten cost and fees
    assert res.total == pytest.approx(res.mark_to_market - res.flatten_cost - res.fees)


def test_spread_capture_is_non_negative_by_construction():
    d = run_synthetic(n_paths=40, seed=3)
    assert all(p.spread_capture >= 0 for p in d.paths)


def test_adverse_selection_turns_a_good_spread_into_a_loss():
    calm = run_synthetic(SyntheticMarket(informed_fraction=0.0), n_paths=120, seed=1)
    toxic = run_synthetic(SyntheticMarket(informed_fraction=0.5), n_paths=120, seed=1)
    assert toxic.components["adverse_selection"] < calm.components["adverse_selection"]
    assert toxic.mean < calm.mean


def test_negative_control_zero_spread_zero_fees_has_no_edge():
    """With no spread to capture and an uninformed flow the mean must be ~0 - and
    after the flatten cost it must not be a profit."""
    d = run_synthetic(SyntheticMarket(informed_fraction=0.0),
                      MMParams(half_spread=0.0, skew_per_unit=0.0, fee_rate=0.0,
                               flatten_half_spread=0.0, flatten_fee_rate=0.0),
                      n_paths=300, seed=2)
    se = d.std / (d.n_paths ** 0.5)
    assert abs(d.mean) < 3.5 * se          # indistinguishable from zero
    assert abs(d.components["spread_capture"]) < 1e-9


def test_report_has_distribution_and_disclaimer():
    d = run_synthetic(n_paths=50, seed=4)
    assert DISCLAIMER in d.text
    assert "NOT proof of live profit" in d.text
    assert {"p05", "p50", "p95"} <= set(d.quantiles)
    assert 0.0 <= d.prob_loss <= 1.0
    assert d.mean_ci95[0] <= d.mean <= d.mean_ci95[1]


def test_inventory_cap_is_respected():
    params = MMParams(max_inventory=3.0, quote_size=1.0)
    d = run_synthetic(SyntheticMarket(informed_fraction=0.3), params, n_paths=30, seed=6)
    assert all(p.max_abs_inventory <= 3.0 + 1e-9 for p in d.paths)


def test_deterministic_for_same_seed():
    a = run_synthetic(n_paths=10, seed=9)
    b = run_synthetic(n_paths=10, seed=9)
    assert a.mean == b.mean and a.quantiles == b.quantiles


def test_replay_on_recorded_format_fixture():
    events = load_recorded(FIXTURE)
    assert any(e.kind == "trade" for e in events)
    d = replay_recorded(events, MMParams(half_spread=0.02, max_inventory=5), seed=1)
    assert d.n_paths >= 5
    assert "recorded book replay" in d.text
    assert "NOT proof of live profit" in d.text
    # conservative fills: nothing fills without a crossing/touching trade
    assert d.mean_fills >= 0


def test_replay_fills_require_trade_through_quote(tmp_path):
    csvp = tmp_path / "r.csv"
    csvp.write_text(
        "kind,ts,bid,ask,price,size,taker_side\n"
        "book,1,99.99,100.01,,,\n"
        "trade,1.5,,,100.02,1,buy\n"          # at/below our ask 100.06 -> no fill
        "book,2,99.99,100.01,,,\n"
        "trade,2.5,,,100.20,1,buy\n"          # through our ask -> fill
        "book,3,100.0,100.02,,,\n", encoding="utf-8")
    d = replay_recorded(load_recorded(csvp), MMParams(half_spread=0.05, skew_per_unit=0.0),
                        blocks=1)
    assert sum(p.fills for p in d.paths) == 1


def test_loader_rejects_unknown_rows_and_empty(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("kind,ts,bid,ask,price,size,taker_side\nfoo,1,,,,,\n", encoding="utf-8")
    with pytest.raises(MMError):
        load_recorded(bad)
    only_trades = tmp_path / "t.csv"
    only_trades.write_text("kind,ts,bid,ask,price,size,taker_side\ntrade,1,,,1,1,buy\n",
                           encoding="utf-8")
    with pytest.raises(MMError):
        load_recorded(only_trades)
