"""Creator fee pump.fun: ставки по снимку mainnet, когорта без отбора, замер только после 24 ч."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bossman.trading_learning.pump_creator_cohort import Cohort, creator_fee_usd, load_tables, summarize, tier_bps

T0 = 1_800_000_000.0
CREATED = datetime.fromtimestamp(T0, timezone.utc).isoformat().replace("+00:00", "Z")


def pool(dex, token, vol=0.0, fdv=0.0):
    return {"relationships": {"dex": {"data": {"id": dex}}, "base_token": {"data": {"id": f"solana_{token}"}}},
            "attributes": {"name": token, "pool_created_at": CREATED, "volume_usd": {"h24": str(vol)},
                           "fdv_usd": str(fdv)}}


def test_snapshot_tables_match_sdk_tier_rule():
    t = load_tables()
    assert tier_bps(t["bonding_curve"]["tiers"], 0) == 30
    assert tier_bps(t["pumpswap"]["tiers"], 100) == 30          # ниже 420 SOL
    assert tier_bps(t["pumpswap"]["tiers"], 420) == 95
    assert tier_bps(t["pumpswap"]["tiers"], 1469.9) == 95
    assert tier_bps(t["pumpswap"]["tiers"], 1470) == 90
    assert tier_bps(t["pumpswap"]["tiers"], 1_000_000) == 5


def test_creator_fee_sums_curve_and_swap():
    f = creator_fee_usd(10_000, 100_000, 100 * 500, 100, load_tables())     # капитализация 500 SOL
    assert f["curve_usd"] == pytest.approx(30.0) and f["swap_usd"] == pytest.approx(950.0)


def test_poll_takes_only_pump_fun_and_measure_waits_24h(tmp_path):
    now = [T0]

    def get(url):
        if "new_pools" in url:
            return {"data": [pool("pump-fun", "A"), pool("meteora-dbc", "B"), pool("pump-fun", "C")]
                    if url.endswith("page=1") else []}
        token = url.split("/tokens/")[1].split("/")[0]
        if token == "A":
            return {"data": [pool("pump-fun", "A", vol=1000), pool("pumpswap", "A", vol=20_000, fdv=50_000)]}
        return {"data": [pool("pump-fun", token, vol=10)]}

    c = Cohort(tmp_path, get=get, now=lambda: now[0])
    assert c.poll() == 2 and c.poll() == 0                        # B не pump.fun; повтор не дублирует
    assert c.measure(100.0) == 0                                  # ещё нет суток
    now[0] = T0 + 24 * 3600 + 60
    assert c.measure(100.0) == 2
    rows = {r["token"]: r["measured"] for r in c.rows()}
    assert rows["A"]["migrated"] and rows["A"]["total_usd"] == pytest.approx(1000 * 0.003 + 20_000 * 0.0095)
    assert rows["C"]["total_usd"] == pytest.approx(0.03)
    s = summarize(c.rows())
    assert s["measured"] == 2 and s["share_ge_100usd"] == 0.5
