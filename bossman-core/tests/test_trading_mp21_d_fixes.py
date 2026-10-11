"""MP21-D regression tests: backtest stop gaps and cohort measurement robustness."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bossman.trading_learning.backtest import _exit_index
from bossman.trading_learning.models import Candle, Decision
from bossman.trading_learning.pump_creator_cohort import Cohort, report_md, summarize
from bossman.trading_learning.strategy import StrategyRule

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(i, o, h, l, c):
    return Candle(ts=T + timedelta(minutes=i), open=o, high=h, low=l, close=c, volume=1.0)


def rule(direction):
    return StrategyRule(rule_id="mp21d", asset="BTC", venue="test", timeframe="1m", direction=direction,
                        stop_pct=0.02, take_pct=0.05, max_hold_bars=5, derived_from=("test",))


def test_long_stop_gap_fills_at_open_not_at_stop():
    r = rule(Decision.LONG)
    # entry 100 -> stop 98; next bar opens at 90 (gap through the stop)
    idx, price = _exit_index(r, 100.0, [bar(1, 90.0, 95.0, 89.0, 92.0)])
    assert (idx, price) == (0, 90.0)
    # no gap: bar opens above the stop and trades through it -> stop price
    idx, price = _exit_index(r, 100.0, [bar(1, 99.0, 99.5, 97.0, 98.0)])
    assert price == 98.0


def test_short_stop_gap_fills_at_open():
    r = rule(Decision.SHORT)
    idx, price = _exit_index(r, 100.0, [bar(1, 110.0, 111.0, 108.0, 109.0)])
    assert price == 110.0


def _pool(dex, token, created, vol=0.0, fdv=0.0):
    return {"relationships": {"dex": {"data": {"id": dex}}, "base_token": {"data": {"id": f"solana_{token}"}}},
            "attributes": {"name": token, "pool_created_at": created, "volume_usd": {"h24": str(vol)},
                           "fdv_usd": str(fdv)}}


def test_one_failing_token_does_not_block_or_discard_the_cohort(tmp_path):
    t0 = 1_800_000_000.0
    created = datetime.fromtimestamp(t0, timezone.utc).isoformat().replace("+00:00", "Z")
    now = [t0]

    def get(url):
        if "new_pools" in url:
            return {"data": [_pool("pump-fun", "BAD", created), _pool("pump-fun", "GOOD", created)]
                    if url.endswith("page=1") else []}
        if "/tokens/BAD/" in url:
            raise RuntimeError("HTTP 404")
        return {"data": [_pool("pump-fun", "GOOD", created, vol=1000)]}

    c = Cohort(tmp_path, get=get, now=lambda: now[0])
    c.poll()
    now[0] = t0 + 24 * 3600 + 60
    assert c.measure(100.0) == 2
    rows = {r["token"]: r["measured"] for r in c.rows()}
    assert rows["BAD"]["unmeasurable"] is True
    assert rows["GOOD"]["total_usd"] > 0
    s = summarize(c.rows())
    assert s["measured"] == 1 and s["unmeasurable"] == 1


def test_late_measurement_is_excluded_from_first_day_stats(tmp_path):
    t0 = 1_800_000_000.0
    created = datetime.fromtimestamp(t0, timezone.utc).isoformat().replace("+00:00", "Z")
    now = [t0]

    def get(url):
        if "new_pools" in url:
            return {"data": [_pool("pump-fun", "LATE", created)] if url.endswith("page=1") else []}
        return {"data": [_pool("pump-fun", "LATE", created, vol=50_000)]}

    c = Cohort(tmp_path, get=get, now=lambda: now[0])
    c.poll()
    now[0] = t0 + 72 * 3600                      # measured 3 days late: h24 is NOT the first day
    c.measure(100.0)
    s = summarize(c.rows())
    assert s["measured"] == 0 and s["late_window_excluded"] == 1
    assert "полнота" in report_md(c.rows(), c.tables)
