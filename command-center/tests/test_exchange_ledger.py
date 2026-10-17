"""Учёт обменника: импорт, отвергнутые строки, отчёт за период, сверка комиссии. Данные синтетические."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bossman_shared.exchange_ledger import fee_check, parse_csv, period_report

HEAD = "ts,side,asset,amount,rate,fee,currency\n"
GOOD = (HEAD +
        "2026-10-01T09:30:00Z,buy,btc,0.5,60000,30,usd\n"
        "2026-10-01T10:00:00Z,sell,btc,0.2,61000,12.2,usd\n"
        "2026-10-02T11:00:00Z,sell,usdt,1000,1.0,10,usd\n"
        "2026-11-01T00:00:00Z,buy,btc,1,60000,60,usd\n")


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_import_and_period_totals_are_exact():
    imp = parse_csv(GOOD)
    assert len(imp.deals) == 4 and imp.rejected == []
    rep = period_report(imp.deals, _utc(2026, 10, 1), _utc(2026, 11, 1))
    assert rep["deals"] == 3                       # сделка 1 ноября в [start,end) не входит
    btc = next(p for p in rep["pairs"] if p["asset"] == "BTC")
    assert btc["paid_out"] == Decimal("30000.0") and btc["received"] == Decimal("12200.0")
    assert btc["cash_flow"] == Decimal("-17800.0") and btc["net_position"] == Decimal("0.3")
    assert btc["fees"] == Decimal("42.2")


def test_bad_rows_are_rejected_with_reason_not_dropped_silently():
    text = (HEAD +
            "2026-10-01T09:30:00Z,hold,btc,1,60000,0,usd\n"      # side
            "2026-10-01T09:30:00Z,buy,btc,-1,60000,0,usd\n"      # amount < 0
            "2026-10-01T09:30:00Z,buy,btc,1,60000,-5,usd\n"      # fee < 0
            "2026-10-01 09:30:00,buy,btc,1,60000,0,usd\n"        # нет часового пояса
            "2026-10-01T09:30:00Z,buy,btc,NaN,60000,0,usd\n"     # не конечное
            "2026-10-01T09:30:00Z,buy,btc,1,60000,0,usd\n")      # единственная годная
    imp = parse_csv(text)
    assert len(imp.deals) == 1
    assert [r.line for r in imp.rejected] == [2, 3, 4, 5, 6]
    assert all(r.reason for r in imp.rejected)


def test_missing_columns_and_bad_period_raise():
    with pytest.raises(ValueError, match="нет колонок"):
        parse_csv("ts,side\n2026-10-01T00:00:00Z,buy\n")
    deals = parse_csv(GOOD).deals
    with pytest.raises(ValueError):
        period_report(deals, _utc(2026, 11, 1), _utc(2026, 10, 1))
    with pytest.raises(ValueError):
        period_report(deals, datetime(2026, 10, 1), _utc(2026, 11, 1))


def test_empty_period_is_zero_deals_not_an_error():
    rep = period_report(parse_csv(GOOD).deals, _utc(2025, 1, 1), _utc(2025, 2, 1))
    assert rep["deals"] == 0 and rep["pairs"] == []


def test_fee_check_flags_only_real_mismatches():
    deals = parse_csv(GOOD).deals
    flagged = fee_check(deals, Decimal("0.1"))      # 0.1% от оборота
    # buy 30000 -> 30 (ок), sell 12200 -> 12.2 (ок), usdt 1000 -> 1 (факт 10, расхождение), 60000 -> 60 (ок)
    assert [f["asset"] for f in flagged] == ["USDT"]
    assert fee_check(deals, Decimal("0.1"), tolerance=Decimal("20")) == []
