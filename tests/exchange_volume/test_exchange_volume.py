"""Tests for tools/exchange_volume. All fixtures are SYNTHETIC (generated here, no real client data).

Run one file at a time: python -m pytest tests/exchange_volume -q
Optional real-shape test: set EXCHANGE_VOLUME_ANON_EXPORT to an anonymised export path (never committed).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from exchange_volume import calc, cli, parse, reconcile  # noqa: E402
from exchange_volume.timeutil import (  # noqa: E402
    business_period_close, prague_offset, prague_to_utc, to_prague)


# ----------------------------------------------------------------- builders
def html_export(items, offset="-08:00"):
    """items: (msg_id, 'DD.MM.YYYY HH:MM:SS', text). Mimics Telegram Desktop messages.html."""
    out = ['<html><!-- SYNTHETIC FIXTURE --><body><div class="history">',
           '<div class="message service" id="message-1"><div class="body details">1 January 2026</div></div>']
    for mid, ts, text in items:
        out.append(
            f'<div class="message default clearfix" id="message{mid}"><div class="body">'
            f'<div class="pull_right date details" title="{ts} UTC{offset}">t</div>'
            f'<div class="from_name">Synthetic Desk</div><div class="text">{text}</div></div></div>')
    out.append("</div></body></html>")
    return "\n".join(out)


def deals_of(items, **kw):
    msgs = parse.parse_export(html_export(items, **kw))
    return msgs, [parse.classify(m) for m in msgs]


def mk(ref, ts, i, ia, o, oa, **kw):
    return calc.Deal("chat", str(ref), ts, i, D(ia), o, D(oa), **kw)


T0 = dt.datetime(2026, 3, 10, 12, 0)


# ----------------------------------------------------------------- time
def test_prague_dst_boundaries():
    assert prague_offset(dt.datetime(2026, 3, 29, 0, 59)) == dt.timedelta(hours=1)
    assert prague_offset(dt.datetime(2026, 3, 29, 1, 0)) == dt.timedelta(hours=2)
    assert prague_offset(dt.datetime(2026, 10, 25, 0, 59)) == dt.timedelta(hours=2)
    assert prague_offset(dt.datetime(2026, 10, 25, 1, 0)) == dt.timedelta(hours=1)
    assert prague_to_utc(to_prague(dt.datetime(2026, 7, 1, 10, 0))) == dt.datetime(2026, 7, 1, 10, 0)


@pytest.mark.parametrize("day,close", [
    (dt.date(2026, 3, 20), dt.date(2026, 3, 20)), (dt.date(2026, 3, 21), dt.date(2026, 4, 20)),
    (dt.date(2026, 12, 21), dt.date(2027, 1, 20)), (dt.date(2026, 1, 1), dt.date(2026, 1, 20))])
def test_business_period_close(day, close):
    assert business_period_close(day) == close


# ----------------------------------------------------------------- parsing
@pytest.mark.parametrize("tok,k,val", [("20 800", False, "20800"), ("534,88", False, "534.88"),
                                       ("1.234,5", False, "1234.5"), ("1,234.5", False, "1234.5"), ("10", True, "10000")])
def test_parse_amount(tok, k, val):
    assert parse.parse_amount(tok, k) == D(val)


def _one(text):
    return parse.classify(parse.Message(1, T0, 0, T0, "S1", text))


def test_classify_direct_and_reverse_and_unmarked():
    c = _one("Принял 500 USDT Выдал 10 400 CZK")
    assert (c.kind, c.in_leg.ccy, c.in_leg.amount, c.out_leg.ccy, c.out_leg.amount) == \
        ("deal", "USDT", D(500), "CZK", D(10400))
    c = _one("Выдал 15000 USDT Принял 314 700 CZK")
    assert (c.in_leg.ccy, c.in_leg.amount, c.out_leg.ccy) == ("CZK", D(314700), "USDT")
    c = _one("500 usdt 10400 czk")
    assert c.kind == "deal" and "direction_assumed_first_is_in" in c.flags


def test_classify_refuses_to_guess():
    assert _one("Принял 100 USDT Выдал 2000 CZK и 500 EUR").kind == "complex"
    assert _one("Принял 100 Выдал 2000 CZK").kind == "incomplete"
    assert _one("Принял 100 USDT Выдал 5000 RUB").kind != "deal"      # unsupported currency
    assert _one("Принял 100 USDT Выдал 100 USDT").kind == "ignored"   # same currency
    assert _one("Принял 23000 USDT (Odtat dolg) 1104 CZK").kind == "debt"
    assert _one("Расход 16$ прописка").kind == "expense"
    assert _one("61800").kind == "ignored"


def test_ambiguous_separator_is_not_guessed():
    assert _one("Принял 99.999 CZK Выдал 17 USDT").kind == "ambiguous_amount"
    assert _one("Принял 1.234,5 CZK Выдал 50 USDT").kind == "deal"        # unambiguous
    assert _one("Принял 20596.333 USDT Выдал 18000 EUR").kind == "deal"   # 5-digit integer part


def test_complex_legs_are_provisional_only():
    c = _one("Принял 1000 USDT Выдал 500 EUR и 20000 CZK")
    assert c.kind == "complex" and "provisional_first_two_pairs" in c.flags
    assert calc.deals_from_chat([c]) == []                      # never exact
    assert len(calc.deals_from_chat([c], include_provisional=True)) == 1


def test_export_timezone_and_anonymised_sender():
    msgs = parse.parse_export(html_export([(5, "20.03.2026 15:30:00", "Принял 100 USDT Выдал 2000 CZK")]))
    m = msgs[0]
    assert m.utc_offset_min == -480 and m.ts_utc == dt.datetime(2026, 3, 20, 23, 30)
    assert m.sender == "S1" and "Synthetic" not in repr(m)


def test_prague_day_differs_from_export_wallclock_day():
    items = [(1, "20.03.2026 14:30:00", "Принял 100 USDT Выдал 2000 CZK"),   # 23:30 Prague, 20th
             (2, "20.03.2026 15:30:00", "Принял 200 USDT Выдал 4000 CZK")]   # 00:30 Prague, 21st
    _, cands = deals_of(items)
    per = calc.aggregate(calc.deals_from_chat(cands))
    assert set(per) == {"2026-03-20", "2026-04-20"}
    assert per["2026-03-20"]["deals_counted"] == 1 and per["2026-04-20"]["deals_counted"] == 1


# ----------------------------------------------------------------- dedup / internal
def test_duplicate_within_window_only_and_negative_control():
    a = mk(1, T0, "USDT", 500, "CZK", 10400)
    b = mk(2, T0 + dt.timedelta(minutes=3), "USDT", 500, "CZK", 10400)       # repost
    c = mk(3, T0 + dt.timedelta(hours=2), "USDT", 500, "CZK", 10400)          # later, same day
    n = mk(4, T0 + dt.timedelta(minutes=4), "USDT", 500, "CZK", "10400.01")   # negative control
    info = calc.mark_duplicates([a, b, c, n])
    assert (a.status, b.status, c.status, n.status) == ("counted", "excluded_duplicate", "counted", "counted")
    assert b.dup_of == "1" and info["same_day_identical_candidates_kept"] == 1


def test_mirror_pair_flagged_not_removed_and_exclusion_list():
    a = mk(1, T0, "USDT", 100, "EUR", 90)
    b = mk(2, T0 + dt.timedelta(hours=1), "EUR", 90, "USDT", 100)
    assert calc.mark_mirror_pairs([a, b]) == 2
    assert a.status == b.status == "counted"
    assert calc.apply_internal_exclusions([a, b], {"2"}) == 1 and b.status == "excluded_internal"


def test_rate_outlier_flagged_but_kept():
    ds = [mk(i, T0 + dt.timedelta(minutes=20 * i), "USDT", 100 + i, "CZK", (100 + i) * 21) for i in range(6)]
    bad = mk(99, T0 + dt.timedelta(days=1), "USDT", 100, "CZK", 21000)       # 210 CZK per USDT
    ds.append(bad)
    assert calc.flag_rate_outliers(ds)["flagged"] == 1
    assert "implied_rate_outlier" in bad.flags and bad.status == "counted"


# ----------------------------------------------------------------- aggregation
def test_gross_volume_never_filled_for_unpriced_deals():
    ds = [mk(1, T0, "USDT", 1000, "CZK", 21000), mk(2, T0, "CZK", 42000, "USDT", 2000),
          mk(3, T0, "EUR", 500, "CZK", 12500)]
    calc.price(ds)
    p = calc.aggregate(ds)["2026-03-20"]
    assert p["gross_usdt_direct"] == "3000" and p["unpriced_deals"] == 1
    assert p["unpriced_pairs"] == ["EUR>CZK"] and p["gross_usdt_converted_external_rate"] is None
    assert p["usdt_received_from_clients"] == "1000" and p["usdt_paid_to_clients"] == "2000"
    assert p["commission_usdt"] is None and p["net_usdt_direct"] is None
    assert D(p["per_pair"]["USDT>CZK"]["vwap_out_per_in"]) == D(21)


def test_external_rates_as_of_and_staleness(tmp_path):
    csvp = tmp_path / "r.csv"
    csvp.write_text("ts_utc,base,quote,rate,source\n2026-03-10T08:00:00,USDT,CZK,20,synthetic\n"
                    "2026-03-10T11:00:00,EUR,USDT,1.10,synthetic\n", encoding="utf-8")
    rb = calc.RateBook.from_csv(str(csvp), 24)
    d_ok = mk(1, T0, "CZK", 2000, "EUR", 80)            # CZK via inverted USDT/CZK = 1/20 -> 100 USDT
    d_eur = mk(2, T0, "EUR", 100, "CZK", 2500)
    d_stale = mk(3, T0 + dt.timedelta(days=3), "CZK", 2000, "EUR", 80)
    calc.price([d_ok, d_eur, d_stale], rb)
    assert d_ok.usdt_volume == D(100) and d_ok.rate_source == "synthetic" and d_ok.rate_ts_utc
    assert d_eur.usdt_volume == D("110.0")
    assert d_stale.usdt_volume is None                   # stale rate -> not priced, not guessed


# ----------------------------------------------------------------- db adapter
def _make_db(path, with_missing_fee=False):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT, from_ccy TEXT, from_amt REAL, "
                "to_ccy TEXT, to_amt REAL, fee REAL, fee_ccy TEXT, created_at TEXT, client_type TEXT)")
    rows = [(1, "completed", "USDT", 1000, "CZK", 21000, 5, "USDT", "2026-03-10 12:00:00", "organic"),
            (2, "completed", "CZK", 42000, "USDT", 2000, 0 if with_missing_fee else 7, "USDT",
             "2026-03-11 12:00:00", "organic"),
            (3, "cancelled", "USDT", 9999, "CZK", 1, 0, None, "2026-03-11 13:00:00", "organic"),
            (4, "completed", "USDT", 50, "EUR", 45, 1, "USDT", "2026-03-12 09:00:00", "ads")]
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_db_adapter_commission_net_and_read_only(tmp_path):
    p = tmp_path / "s.db"
    _make_db(str(p))
    before = p.read_bytes()
    ds = calc.deals_from_db(str(p))
    assert [d.ref for d in ds] == ["1", "2", "4"]            # cancelled excluded
    calc.mark_duplicates(ds)
    calc.price(ds)
    pr = calc.aggregate(ds)["2026-03-20"]
    assert (D(pr["gross_usdt_direct"]), D(pr["commission_usdt"]), D(pr["net_usdt_direct"])) ==         (D(3050), D(13), D(3037))
    assert p.read_bytes() == before                           # never written
    assert [d.ref for d in calc.deals_from_db(str(p), include_ads=False)] == ["1", "2"]


def test_net_is_null_when_any_fee_unknown(tmp_path):
    p = tmp_path / "s.db"
    _make_db(str(p), with_missing_fee=True)
    ds = calc.deals_from_db(str(p), include_ads=False)
    calc.price(ds)
    pr = calc.aggregate(ds)["2026-03-20"]
    assert pr["commission_usdt"] is None and pr["net_usdt_direct"] is None and pr["fee_known_deals"] == 1


# ----------------------------------------------------------------- reconcile
def test_ledger_reconcile_with_negative_control():
    ds = [mk(1, T0, "USDT", 500, "CZK", 10400), mk(2, T0, "USDT", 700, "CZK", 14500)]
    ledger = [(dt.date(2026, 3, 10), "USDT", D(500), "CZK", D(10400), D(500), "x", None),
              (dt.date(2026, 3, 10), "USDT", D(700), "CZK", D(14500), D(700), "x", None)]
    ok = reconcile.reconcile_ledger(ds, ledger)
    assert ok["matched"] == 2 and not ok["only_in_chat_refs"] and not ok["only_in_ledger_row_indexes"]
    ledger[1] = (dt.date(2026, 3, 10), "USDT", D(700), "CZK", D(14501), D(700), "x", None)   # 1 CZK off
    bad = reconcile.reconcile_ledger(ds, ledger)
    assert bad["matched"] == 1 and bad["only_in_chat_refs"] == ["2"] and bad["only_in_ledger_row_indexes"] == [1]


# ----------------------------------------------------------------- end to end + anonymised copy
def _run(tmp_path, export, name):
    a = type("A", (), dict(export=str(export), db=None, db_time_zone="Europe/Prague", exclude_ads=False,
                           period="business", dup_window_min=10, exclusions=None, rates=None,
                           rate_max_age_h=24, workbook=None, baseline=None, out=str(tmp_path / name)))()
    return cli.run(a)


SYN = [(1, "10.03.2026 08:00:00", "Принял 1000 USDT Выдал 21 000 CZK"),
       (2, "10.03.2026 08:03:00", "Принял 1000 USDT Выдал 21 000 CZK"),
       (3, "11.03.2026 09:00:00", "Принял 42 000 CZK Выдал 2000 USDT"),
       (4, "11.03.2026 09:30:00", "Принял 500 EUR Выдал 12 500 CZK"),
       (5, "11.03.2026 10:00:00", "Принял 100 USDT Выдал 2000 CZK и 50 EUR"),
       (6, "11.03.2026 10:10:00", "привет")]


def test_end_to_end_and_anonymised_copy_keeps_structure(tmp_path):
    exp = tmp_path / "messages.html"
    exp.write_text(html_export(SYN), encoding="utf-8")
    r = _run(tmp_path, exp, "run")
    p = r["periods"]["2026-03-20"]
    assert (p["deals_counted"], p["excluded_duplicates"], p["gross_usdt_direct"], p["unpriced_deals"]) == \
        (3, 1, "3000", 1)
    assert p["provisional_complex_messages"] == 1 and r["message_classification"]["kinds"]["complex"] == 1
    assert (tmp_path / "run" / "deals.csv").exists()
    anon = tmp_path / "anon.html"
    cli.anonymise(type("A", (), dict(export=str(exp), out=str(anon), seed=7))())
    text = anon.read_text(encoding="utf-8")
    assert "Synthetic Desk" not in text and "привет" not in text
    q = _run(tmp_path, anon, "run2")["periods"]["2026-03-20"]
    assert q["deals_counted"] == p["deals_counted"] and q["excluded_duplicates"] == p["excluded_duplicates"]
    ratio = D(q["gross_usdt_direct"]) / D(p["gross_usdt_direct"])
    assert D("0.69") < ratio < D("1.41")                      # seeded factor 0.70..1.40, rounding only
    json.loads((tmp_path / "run2" / "volume_result.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not os.environ.get("EXCHANGE_VOLUME_ANON_EXPORT"), reason="local anonymised copy not provided")
def test_real_shape_anonymised_copy(tmp_path):
    r = _run(tmp_path, os.environ["EXCHANGE_VOLUME_ANON_EXPORT"], "real")
    assert r["periods"] and r["message_classification"]["kinds"]["deal"] > 0
