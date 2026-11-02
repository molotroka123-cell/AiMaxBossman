"""Pump.fun Projector: discovery from a fake read-only source, transparent risk, paper P&L math, source failure."""
import json
import time

import pytest

from bossman.trading_learning import projector as P
from bossman.trading_learning.sol_arena import DEX_BOOSTS, DEX_PROFILES, DEX_TOKENS


def pair(mint, *, liq=80_000.0, price=0.001, age_min=600, buys=30, sells=10, h1=10.0, m5=2.0, vol_h1=20_000.0,
         fdv=500_000.0, dex="pumpswap"):
    return {"chainId": "solana", "dexId": dex, "url": f"https://dexscreener.com/solana/{mint}",
            "baseToken": {"address": mint, "symbol": mint[:4].upper(), "name": mint},
            "priceUsd": str(price), "liquidity": {"usd": liq}, "fdv": fdv,
            "priceChange": {"m5": m5, "h1": h1, "h6": 5}, "volume": {"m5": 1000, "h1": vol_h1},
            "txns": {"m5": {"buys": buys, "sells": sells}, "h1": {"buys": buys * 10, "sells": sells * 10}},
            "pairCreatedAt": (time.time() - age_min * 60) * 1000}


def fake_fetch(pairs):
    def get(url):
        if url in (DEX_PROFILES, DEX_BOOSTS):
            return [{"chainId": "solana", "tokenAddress": p["baseToken"]["address"]} for p in pairs]
        if url.startswith(DEX_TOKENS):
            wanted = url[len(DEX_TOKENS):].split(",")
            return [p for p in pairs if p["baseToken"]["address"] in wanted]
        raise AssertionError(url)
    return get


def test_discover_live_and_pump_filter():
    pairs = [pair("goodmint1"), pair("raydmint2", dex="raydium")]
    out = P.discover(fake_fetch(pairs))
    assert out["status"] == "LIVE" and len(out["tokens"]) == 2 and out["fetched_at"]
    assert [t["mint"] for t in P.discover(fake_fetch(pairs), pump_only=True)["tokens"]] == ["goodmint1"]


def test_source_failure_is_reported_not_invented():
    def broken(url):
        raise TimeoutError("down")
    out = P.discover(broken)
    assert out["status"] == "SOURCE_UNAVAILABLE" and out["tokens"] == [] and len(out["errors"]) == 2


def test_risk_reasons_are_visible_and_ordered():
    safe = P.risk_score(P.snapshot(pair("safe1")))
    assert safe["level"] == "LOW" and safe["score"] < 30
    risky = P.risk_score(P.snapshot(pair("rug1", liq=5_000, age_min=10, buys=2, sells=20, h1=-70, vol_h1=60_000,
                                         fdv=900_000)), holders_top10_pct=80)
    assert risky["level"] == "HIGH" and risky["score"] == 100
    joined = " ".join(risky["reasons"])
    for word in ("ликвидность", "мин", "продаж", "обвал", "объём", "FDV", "топ-10"):
        assert word in joined
    missing = P.risk_score({"liq_usd": 0, "age_min": None})
    assert "нет данных о ликвидности" in missing["reasons"]


def test_paper_pnl_math_with_fees_and_impact():
    book = P.PaperBook(cash_usd=100, fee_bps=100, size_usd=10, take_profit_pct=30)
    t = {"mint": "m1", "symbol": "M1", "price_usd": 1.0, "liq_usd": 2_000_000.0}
    buy = book.buy(t, now="2026-11-02T00:00:00+00:00")
    imp = P.PaperBook.impact_pct(10, 2_000_000)
    assert buy["kind"] == "BUY" and book.cash_usd == pytest.approx(90)
    pos = book.positions[0]
    assert pos.entry_price == pytest.approx(1.0 * (1 + imp / 100))
    assert pos.qty == pytest.approx(9.9 / pos.entry_price)
    up = dict(t, price_usd=1.5)
    sells = book.mark({"m1": up}, now="2026-11-02T00:10:00+00:00", now_ts=0)
    assert sells and sells[0]["kind"] == "SELL" and pos.reason == "TAKE_PROFIT"
    exit_imp = P.PaperBook.impact_pct(pos.qty * 1.5, 2_000_000)
    expected = pos.qty * 1.5 * (1 - exit_imp / 100) * 0.99
    assert pos.proceeds_usd == pytest.approx(expected)
    m = book.metrics({})
    assert m["realized_pnl_usd"] == pytest.approx(expected - 10, abs=1e-4) and m["evidence_class"] == "SIMULATED"


def test_thin_liquidity_trade_is_rejected_like_a_failed_tx():
    book = P.PaperBook(size_usd=10, max_impact_pct=3)
    rej = book.buy({"mint": "thin", "price_usd": 1.0, "liq_usd": 200.0}, now="2026-11-02T00:00:00+00:00")
    assert rej["kind"] == "REJECTED" and "impact" in rej["note"] and book.cash_usd == 100


def test_refresh_persists_snapshot_history_and_book(tmp_path):
    pairs = [pair("goodmint1", liq=500_000), pair("rugmint2", liq=3_000, age_min=5)]
    run = P.refresh(tmp_path, fake_fetch(pairs))
    assert run["status"] == "LIVE" and run["tokens"][0]["mint"] == "goodmint1"
    assert run["paper_actions"][-1]["kind"] == "BUY"
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["fetched_at"] == run["fetched_at"]
    assert len(list((tmp_path / "history").glob("*.json"))) == 1
    again = P.refresh(tmp_path, fake_fetch(pairs))
    assert again["paper"]["open_positions"] == 1  # no duplicate entry on the same token
