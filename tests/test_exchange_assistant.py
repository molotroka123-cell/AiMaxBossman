"""Exchange assistant: quotes, rate provenance, deals, reversal, dashboard, rights, backup/restore. Synthetic data only."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

import pytest

from bossman_shared.exchange_assistant import Assistant, QuoteError, Rate, RateBook, RateError, build_quote
from bossman_shared.exchange_assistant.agent import business_month
from bossman_shared.exchange_assistant.rates import load_official
from bossman_shared.exchange_assistant.store import Store, StoreError

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
CENT = Decimal("0.01")


def official(pair, value, at=NOW - timedelta(hours=2), src="ЧНБ"):
    return Rate(pair, Decimal(value), src, at, "official")


def manual(pair, value, at=NOW - timedelta(minutes=5), ttl=timedelta(hours=12)):
    return Rate(pair, Decimal(value), "тест", at, "manual", at + ttl)


def book(**over):
    rates = {"USDCZK": official("USDCZK", "21.600"), "EURCZK": official("EURCZK", "24.325"),
             "EURUSD": official("EURUSD", "1.1250", src="ЕЦБ"), "USDTUSD": manual("USDTUSD", "0.9990")}
    rates.update(over)
    return RateBook([r for r in rates.values() if r is not None])


def fx_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE fx_obs(id INTEGER PRIMARY KEY, observed_at TEXT, minute TEXT, pair TEXT, rate TEXT, source TEXT, "
                "source_url TEXT, rate_date TEXT, raw_text_excerpt TEXT, parser TEXT, flags TEXT, model TEXT)")
    for ts, pair, rate, src, parser, flags in rows:
        con.execute("INSERT INTO fx_obs(observed_at, minute, pair, rate, source, source_url, raw_text_excerpt, parser, flags) "
                    "VALUES (?,?,?,?,?,'u','x',?,?)", (ts, ts[:16], pair, rate, src, parser, flags))
    con.commit()
    con.close()


@pytest.fixture()
def assistant(tmp_path):
    fx = tmp_path / "fx.db"
    t = (NOW - timedelta(hours=3)).isoformat()
    fx_db(fx, [(t, "USDCZK", "21.600", "cnb", "regex", ""), (t, "EURCZK", "24.325", "cnb", "regex", ""),
               (t, "EURUSD", "1.1250", "ecb", "regex", "")])
    store = Store(tmp_path / "assistant.db")
    clock = {"now": NOW}
    a = Assistant(store, fx_db=fx, owner_ids={"owner"}, clock=lambda: clock["now"], stop_file=tmp_path / "STOP")
    a.clock_ref = clock
    yield a
    store.close()


def _qid(text):
    return next(w for w in text.split() if w.startswith("Q-"))


# ------------------------------------------------------------------ quote maths
def test_usdt_to_czk_applies_3pct_and_rounds_down():
    q = build_quote(book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("500"), now=NOW)
    mid = Decimal("0.9990") / (Decimal(1) / Decimal("21.600"))
    assert q.mid == mid
    assert q.get_amount == (Decimal("500") * mid * Decimal("0.97")).quantize(CENT, rounding=ROUND_DOWN)
    assert q.get_amount == Decimal("10465.52")
    assert q.valid_until == NOW + timedelta(minutes=15)
    assert "(1 − 0.03)" in q.formula and "USDT/USD 0.9990" in q.formula


def test_czk_to_usdt_also_carries_margin():
    q = build_quote(book(), give_ccy="CZK", get_ccy="USDT", give_amount=Decimal("10000"), now=NOW)
    fair = Decimal("10000") / Decimal("21.600") / Decimal("0.9990")
    assert q.get_amount < fair
    assert abs(q.get_amount - fair * Decimal("0.97")) < CENT


def test_round_trip_never_profits_the_client():
    b = book()
    q1 = build_quote(b, give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("1000"), now=NOW)
    q2 = build_quote(b, give_ccy="CZK", get_ccy="USDT", give_amount=q1.get_amount, now=NOW)
    assert q2.get_amount < Decimal("1000") * Decimal("0.95")


def test_get_side_rounds_what_client_gives_up_and_covers_target():
    q = build_quote(book(), give_ccy="USDT", get_ccy="CZK", get_amount=Decimal("10000"), now=NOW)
    assert q.fixed_side == "get" and q.get_amount == Decimal("10000.00")
    assert (q.give_amount * q.client_rate).quantize(CENT, rounding=ROUND_DOWN) >= Decimal("10000.00")


def test_eur_czk_uses_direct_cnb_cross():
    q = build_quote(book(), give_ccy="EUR", get_ccy="CZK", give_amount=Decimal("100"), now=NOW)
    assert q.mid == Decimal("24.325") and q.get_amount == Decimal("2359.52")
    assert len(q.rates) == 1


def test_usdt_is_never_silently_usd():
    with pytest.raises(QuoteError, match="USDT/USD"):
        build_quote(book(USDTUSD=None), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("100"), now=NOW)


def test_stale_official_rate_is_refused():
    old = official("USDCZK", "21.6", at=NOW - timedelta(days=5))
    with pytest.raises(QuoteError, match="устарел"):
        build_quote(book(USDCZK=old), give_ccy="USD", get_ccy="CZK", give_amount=Decimal("100"), now=NOW)


def test_expired_manual_rate_is_refused():
    exp = manual("USDTUSD", "0.999", at=NOW - timedelta(hours=13))
    with pytest.raises(QuoteError, match="просрочен"):
        build_quote(book(USDTUSD=exp), give_ccy="USDT", get_ccy="USD", give_amount=Decimal("100"), now=NOW)


def test_missing_rate_and_bad_inputs():
    with pytest.raises(QuoteError, match="нет курса"):
        build_quote(book(EURCZK=None), give_ccy="EUR", get_ccy="CZK", give_amount=Decimal("1"), now=NOW)
    with pytest.raises(QuoteError):
        build_quote(book(), give_ccy="USDT", get_ccy="USDT", give_amount=Decimal("1"), now=NOW)
    with pytest.raises(QuoteError):
        build_quote(book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("-1"), now=NOW)
    with pytest.raises(QuoteError):
        build_quote(book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("1"), get_amount=Decimal("1"), now=NOW)
    with pytest.raises(QuoteError, match="нулю"):
        build_quote(book(), give_ccy="CZK", get_ccy="USDT", give_amount=Decimal("0.1"), now=NOW)
    with pytest.raises(RateError):
        RateBook([official("USDCZK", "2160")])          # implausible: off by 100


def test_quote_is_deterministic():
    a = build_quote(book(), give_ccy="USDT", get_ccy="EUR", give_amount=Decimal("250"), now=NOW)
    b = build_quote(book(), give_ccy="USDT", get_ccy="EUR", give_amount=Decimal("250"), now=NOW)
    assert a == b and a.id.startswith("Q-")


# ------------------------------------------------------------------ assistant flow
def test_full_flow_quote_confirm_duplicate_reverse_dashboard(assistant):
    assert "USDT/USD" in assistant.handle("500 usdt в czk при 3%", "owner")        # no USDT/USD yet -> refused
    assert "Записан ручной курс" in assistant.handle("курс usdt/usd 0,999 источник биржа binance", "owner")
    # Without explicit manager margin, quote is refused and not saved
    assert "явную ставку" in assistant.handle("500 usdt в czk", "owner")
    q = assistant.handle("500 usdt в czk при 3%", "owner")
    assert "10465.52 CZK" in q and "ЧНБ" in q and "наценка 3%" in q
    qid = _qid(q)
    # Without actual legs of both sides, CRM asks for actual amounts and does not record a settled deal
    ask_actuals = assistant.handle(f"обмен выполнен {qid}", "owner")
    assert "фактические суммы обеих сторон" in ask_actuals and "неизвестно" in ask_actuals
    assert assistant.store.deals() == []
    assert "Сделка записана: D-1" in assistant.handle(f"обмен выполнен {qid} приняли 500 выдали 10465,52", "owner")
    assert "повтор не создан" in assistant.handle(f"обмен выполнен {qid} приняли 500 выдали 10465,52", "owner")
    assert len(assistant.store.deals()) == 1
    dash = assistant.handle("итоги", "owner")
    assert "Сделок: 1" in dash and "500.00 USDT" in dash and "N/A" in dash
    assert "CZK -10465.52" in dash and "USDT +500.00" in dash
    assert "отменена" in assistant.handle("отмена D-1 клиент вернул деньги", "owner")
    assert "Сделок: 0 (отменено: 1)" in assistant.handle("итоги", "owner")
    assert sum(l["amount"] for l in assistant.store.legs() if l["ccy"] == "USDT") == 0
    assert "уже отменена" in assistant.handle("отмена D-1 ещё раз", "owner")


def test_expired_quote_cannot_be_confirmed(assistant):
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    qid = _qid(assistant.handle("получить 10000 czk за usdt при 3%", "owner"))
    assistant.clock_ref["now"] = NOW + timedelta(minutes=16)
    assert "просрочена" in assistant.handle(f"обмен выполнен {qid}", "owner")
    assert assistant.store.deals() == []


def test_non_owner_gets_nothing(assistant):
    for cmd in ("курс", "500 usdt в czk при 3%", "итоги", "курс usdt/usd 1 источник x", "обмен выполнен Q-00000000"):
        assert assistant.handle(cmd, "stranger") == "Помощник обменника доступен только владельцу."
    assert assistant.store.manual_rates() == [] and assistant.store.deals() == []


def test_rates_answer_shows_source_and_time(assistant):
    out = assistant.handle("курс", "owner")
    assert "USD/CZK = 21.600 (ЧНБ, 19.10.2026 07:00 UTC)" in out
    assert "EUR/USD = 1.1250 (ЕЦБ" in out
    assert "USDT/USD: нет курса" in out


def test_disputed_and_model_rows_are_ignored(tmp_path):
    fx = tmp_path / "fx.db"
    t = NOW.isoformat()
    fx_db(fx, [(t, "USDCZK", "21.6", "cnb", "regex", "disagreement"), (t, "EURCZK", "24.3", "cnb", "model", "")])
    assert load_official(fx) == []
    assert load_official(tmp_path / "missing.db") == []


def test_unknown_command_and_manual_rate_bounds(assistant):
    assert "Не понял" in assistant.handle("сделай что-нибудь", "owner")
    assert "вне разумных границ" in assistant.handle("курс usdt/usd 5 источник x", "owner")


# ------------------------------------------------------------------ store safety
def test_deals_and_events_cannot_be_deleted(assistant):
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    qid = _qid(assistant.handle("100 usdt в eur при 3%", "owner"))
    assistant.handle(f"обмен выполнен {qid} приняли 100 выдали 86,13", "owner")
    with pytest.raises(sqlite3.DatabaseError):
        assistant.store.db.execute("DELETE FROM deals")
    with pytest.raises(sqlite3.DatabaseError):
        assistant.store.db.execute("UPDATE events SET kind='x'")


def test_backup_and_restore_roundtrip(assistant, tmp_path):
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    qid1 = _qid(assistant.handle("100 usdt в eur при 3%", "owner"))
    assistant.handle(f"обмен выполнен {qid1} приняли 100 выдали 86,13", "owner")
    copy = assistant.store.backup(tmp_path / "bk" / "copy.db")
    Store.verify(copy)
    qid2 = _qid(assistant.handle("200 usdt в eur при 3%", "owner"))
    assistant.handle(f"обмен выполнен {qid2} приняли 200 выдали 172,27", "owner")
    assert len(assistant.store.deals()) == 2
    assistant.store.restore(copy)
    assert len(assistant.store.deals()) == 1
    assert assistant.store.path.with_suffix(".before-restore.db").exists()
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all" * 50)
    with pytest.raises(StoreError):
        assistant.store.restore(bad)
    assert len(assistant.store.deals()) == 1


def test_global_stop_file_blocks_writes_everywhere(assistant):
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    qid = _qid(assistant.handle("100 usdt в czk при 3%", "owner"))
    assistant.stop_file.write_text("stop")
    assert "СТОП" in assistant.handle(f"обмен выполнен {qid} приняли 100 выдали 2093,10", "owner")
    assert "СТОП" in assistant.handle("отмена D-1 x", "owner")
    assert "Курсы" in assistant.handle("курс", "owner")             # reading still works
    assert assistant.store.deals() == []
    assistant.stop_file.unlink()
    assert "Сделка записана" in assistant.handle(f"обмен выполнен {qid} приняли 100 выдали 2093,10", "owner")


def test_two_surfaces_share_one_book(assistant, tmp_path):
    """Pult and phone app open their own Assistant on the same file: a quote made in one is confirmed in the other."""
    other = Assistant(Store(assistant.store.path), fx_db=assistant.fx_db, clock=assistant.clock, stop_file=assistant.stop_file)
    try:
        assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
        qid = _qid(other.handle("100 usdt в czk при 3%", "owner"))
        assert "Сделка записана: D-1" in assistant.handle(f"обмен выполнен {qid} приняли 100 выдали 2093,10", "owner")
        assert "повтор не создан" in other.handle(f"обмен выполнен {qid} приняли 100 выдали 2093,10", "owner")
        assert len(other.store.deals()) == 1
    finally:
        other.store.close()


def test_business_month_21_to_20():
    s, e = business_month(datetime(2026, 10, 19, 12, tzinfo=timezone.utc))
    assert (s.month, s.day, e.month, e.day) == (9, 21, 10, 21)
    s, e = business_month(datetime(2026, 12, 25, 12, tzinfo=timezone.utc))
    assert (s.year, s.month, e.year, e.month) == (2026, 12, 2027, 1)


# ------------------------------------------------------------------ Tier 5 Argon: Agentforce + Xe extensions
def test_synthetic_1000_usdt_at_25_czk_with_3pct_buy_and_sell():
    """Owner canonical check: 1 000 USDT at mid 25 CZK/USD and 3% fee ->
    client buys 1 000 USDT = 25 750.00 CZK; client sells 1 000 USDT = 24 250.00 CZK."""
    b = book(USDCZK=official("USDCZK", "25.000"), USDTUSD=manual("USDTUSD", "1.0000"))
    # Client sells 1 000 USDT -> receives 24 250.00 CZK
    q_sell = build_quote(
        b, give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("1000"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW,
    )
    assert q_sell.get_amount == Decimal("24250.00")
    assert q_sell.client_rate == Decimal("24.250000")

    # Client buys 1 000 USDT for CZK -> pays 25 750.00 CZK
    q_buy = build_quote(
        b, give_ccy="CZK", get_ccy="USDT", get_amount=Decimal("1000"),
        margin=Decimal("0.03"), require_explicit_margin=True, client_buys_get=True, now=NOW,
    )
    assert q_buy.give_amount == Decimal("25750.00")
    assert q_buy.get_amount == Decimal("1000.00")
    assert "(1 + 0.03)" in q_buy.formula


def test_require_explicit_margin_refuses_implicit_fee():
    with pytest.raises(QuoteError, match="явную ставку"):
        build_quote(
            book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("500"),
            margin=None, require_explicit_margin=True, now=NOW,
        )


def test_assistant_explicit_fee_and_buy_command(assistant):
    assistant.handle("курс usdt/usd 1.0000 источник синтетика", "owner")
    assistant.handle("курс usd/czk 25.000 источник синтетика", "owner")
    reply_buy = assistant.handle("купить 1000 usdt за czk при 3%", "owner")
    assert "Клиент отдаёт: 25750.00 CZK" in reply_buy
    assert "Клиент получает: 1000.00 USDT" in reply_buy
    assert "наценка 3%" in reply_buy

    reply_sell = assistant.handle("1000 usdt в czk при 3%", "owner")
    assert "Клиент отдаёт: 1000.00 USDT" in reply_sell
    assert "Клиент получает: 24250.00 CZK" in reply_sell


def test_crm_statuses_actual_legs_and_branded_document(assistant):
    from bossman_shared.exchange_assistant import render_branded_document

    assistant.handle("курс usdt/usd 1.0000 источник синтетика", "owner")
    assistant.handle("курс usd/czk 25.000 источник синтетика", "owner")
    q_draft = build_quote(
        assistant.book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("500"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW - timedelta(seconds=5),
    )
    assistant.store.save_quote(q_draft, crm_status="draft")
    with pytest.raises(StoreError, match=r"\(draft\).*\(quoted\)"):
        assistant.store.confirm(q_draft.id, NOW, "owner", crm_status="accepted")

    q = build_quote(
        assistant.book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("1000"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW,
    )
    assistant.store.save_quote(q, crm_status="quoted")
    saved_q = assistant.store.quote(q.id)
    assert saved_q["crm_status"] == "quoted"

    # RT20-04: accepted without actual legs records 'unknown' and updates quote status in same transaction
    deal_acc, created_acc = assistant.store.confirm(q.id, NOW, "owner", crm_status="accepted")
    assert created_acc is True
    assert deal_acc["crm_status"] == "accepted"
    assert assistant.store.quote(q.id)["crm_status"] == "accepted"
    assert deal_acc["leg_in_actual"] == "unknown"
    assert deal_acc["leg_out_actual"] == "unknown"
    assert deal_acc["slippage_in"] == "unknown"
    assert deal_acc["slippage_out"] == "unknown"

    # Settling transitions accepted -> settled with actual legs
    deal, settled_ok = assistant.store.confirm(
        q.id, NOW, "owner", crm_status="settled",
        leg_in_actual=Decimal("1000.00"), leg_out_actual=Decimal("24250.00"),
    )
    assert settled_ok is True
    assert deal["crm_status"] == "settled"
    assert assistant.store.quote(q.id)["crm_status"] == "settled"
    assert deal["leg_in_actual"] == "1000.00"
    assert deal["leg_out_actual"] == "24250.00"
    assert deal["fee_rate"] == "0.03"

    html_doc = render_branded_document(q, deal=deal)
    assert "<!DOCTYPE html>" in html_doc
    assert "<svg" in html_doc
    assert q.id in html_doc
    assert "24 250,00 CZK" in html_doc
    assert "3%" in html_doc

    rev = assistant.store.reverse(deal["id"], "клиент отменил", NOW, "owner")
    assert rev["status"] == "reversed"
    assert rev["crm_status"] == "cancelled"
    assert assistant.store.quote(q.id)["crm_status"] == "cancelled"


def test_xe_provenance_and_fallback_without_credentials():
    from bossman_shared.exchange_assistant import licensed_xe_feed_status

    st_missing = licensed_xe_feed_status({})
    assert st_missing["licensed_feed_configured"] is False
    assert st_missing["status"] == "N/A"
    assert st_missing["fallback_mode"] == "MANUAL_ENTRY"

    st_present = licensed_xe_feed_status({"XE_API_ID": "id1", "XE_API_KEY": "key1"})
    assert st_present["licensed_feed_configured"] is True
    assert st_present["status"] == "LICENSED_API_READY"

    r = official("USDCZK", "25.000")
    prov = r.provenance()
    assert prov["pair"] == "USDCZK"
    assert prov["source"] == "ЧНБ"
    assert prov["timestamp_utc"].endswith("+00:00")
    assert prov["raw_receipt"].startswith("ЧНБ:USDCZK:")


def test_load_official_invalidates_cache_on_wal_writes(tmp_path):
    """When fx.db is opened in WAL mode and updated without checkpointing to the main file,
    load_official must see the new WAL state instead of returning a stale cached rate."""
    fx = tmp_path / "fx_wal.db"
    t1 = (NOW - timedelta(hours=2)).isoformat()
    t2 = (NOW - timedelta(hours=1)).isoformat()
    con = sqlite3.connect(fx)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE fx_obs(id INTEGER PRIMARY KEY, observed_at TEXT, minute TEXT, pair TEXT, "
            "rate TEXT, source TEXT, source_url TEXT, rate_date TEXT, raw_text_excerpt TEXT, "
            "parser TEXT, flags TEXT, model TEXT)"
        )
        con.execute(
            "INSERT INTO fx_obs(observed_at, minute, pair, rate, source, source_url, raw_text_excerpt, parser, flags) "
            "VALUES (?,?,?,?,?,'u','x',?,?)",
            (t1, t1[:16], "USDCZK", "21.600", "cnb", "regex", ""),
        )
        con.commit()
        r1 = load_official(fx)
        assert len(r1) == 1 and r1[0].value == Decimal("21.600")

        # Write a newer row in WAL without closing/checkpointing
        con.execute(
            "INSERT INTO fx_obs(observed_at, minute, pair, rate, source, source_url, raw_text_excerpt, parser, flags) "
            "VALUES (?,?,?,?,?,'u','x',?,?)",
            (t2, t2[:16], "USDCZK", "22.100", "cnb", "regex", ""),
        )
        con.commit()
        r2 = load_official(fx)
        assert len(r2) == 1 and r2[0].value == Decimal("22.100")
    finally:
        con.close()


def test_dashboard_uses_actual_legs_and_cancel_quote(assistant):
    """Store.dashboard() must use leg_in_actual/leg_out_actual when present, and
    Store.cancel_quote() transitions unconfirmed quotes to crm_status='cancelled'."""
    assistant.handle("курс usdt/usd 1.0000 источник синтетика", "owner")
    assistant.handle("курс usd/czk 25.000 источник синтетика", "owner")
    q1 = build_quote(
        assistant.book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("1000"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW,
    )
    assert q1.direction == "SELL"
    assert q1.manager_margin_decision == "explicit"
    assistant.store.save_quote(q1, crm_status="quoted")
    saved_q1 = assistant.store.quote(q1.id)
    assert saved_q1["direction"] == "SELL"
    assert saved_q1["manager_margin_decision"] == "explicit"

    # Client actually handed over 950 USDT and received 23037.50 CZK via Assistant command
    reply = assistant.handle(f"обмен выполнен {q1.id} приняли 950 выдали 23037,50", "owner")
    assert "Приняли 950.00 USDT, выдали 23037.50 CZK" in reply
    deals = assistant.store.deals()
    assert len(deals) == 1
    assert deals[0]["slippage_in"] == "-50.00"
    assert deals[0]["slippage_out"] == "-1212.50"
    assert deals[0]["idempotency_key"] == q1.id

    dash = assistant.store.dashboard(*business_month(NOW))
    assert dash["turnover_usdt"] == Decimal("950.00")
    assert dash["position_change"]["USDT"] == Decimal("950.00")
    assert dash["position_change"]["CZK"] == Decimal("-23037.50")

    # Cancel an unconfirmed quote
    q2 = build_quote(
        assistant.book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("500"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW + timedelta(seconds=1),
    )
    assistant.store.save_quote(q2, crm_status="draft")
    cancelled = assistant.store.cancel_quote(q2.id, "клиент передумал", NOW + timedelta(seconds=2), "owner")
    assert cancelled["crm_status"] == "cancelled"
    with pytest.raises(StoreError, match="уже есть сделка"):
        assistant.store.cancel_quote(q1.id, "поздно", NOW + timedelta(seconds=3), "owner")


def test_swapme_pdf_receipt_and_daily_pult_summary(assistant):
    """Tier-1 Step (7): PDF receipt with SwapMe logo after deal confirmation and 24h daily Pult report."""
    from bossman_shared.exchange_assistant import render_pdf_document

    assistant.handle("курс usdt/usd 1.0000 источник синтетика", "owner")
    assistant.handle("курс usd/czk 25.000 источник синтетика", "owner")
    q_reply = assistant.handle("1000 usdt в czk при 3%", "owner")
    qid = _qid(q_reply)

    confirm_reply = assistant.handle(f"обмен выполнен {qid} приняли 1000 выдали 24250", "owner")
    assert "Сделка записана: D-1" in confirm_reply
    assert "PDF" in confirm_reply

    filename, pdf_bytes = assistant.deal_pdf(1)
    assert filename == f"SwapMe-D-1-{qid}.pdf"
    assert pdf_bytes.startswith(b"%PDF-1.4\n")
    assert pdf_bytes.endswith(b"%%EOF\n")
    assert b"SwapMe Exchange" in pdf_bytes
    assert f"D-1 ({qid})".encode("utf-8") in pdf_bytes
    assert b"24 250,00 CZK" in pdf_bytes
    assert b"1 000,00 USDT" in pdf_bytes
    assert b"3%" in pdf_bytes

    # Deterministic: calling render_pdf_document twice produces identical bytes
    deal = assistant.store.deals()[0]
    q_obj = assistant.store.quote_object(qid)
    assert render_pdf_document(q_obj, deal=deal) == pdf_bytes

    # Receipt file persisted on disk in store's receipts directory
    pdf_path = assistant.store.receipt_pdf_path(1)
    assert pdf_path.is_file() and pdf_path.read_bytes() == pdf_bytes

    # Assistant command for receipt and daily report
    rec_msg = assistant.handle("чек D-1", "owner")
    assert f"SwapMe-D-1-{qid}.pdf" in rec_msg and "SHA-256:" in rec_msg

    daily = assistant.handle("суточный отчет", "owner")
    assert "Суточный отчёт SwapMe" in daily
    assert "D-1" in daily and "1000.00 USDT" in daily and f"SwapMe-D-1-{qid}.pdf" in daily


def test_redteam_rt20_stop_quote_explicit_buy_margin_and_manual_rate_ordering(assistant):
    """Regression coverage for RT20-01 (STOP blocks quote creation at Assistant and Store levels),
    RT20-02 (every quote requires explicit manager margin), RT20-03 (newest manual rate wins),
    and RT20-04 (Store.confirm refuses settled without actual legs and Assistant asks for both actual legs)."""
    assistant.handle("курс usdt/usd 1.0000 источник синтетика", "owner")
    assistant.handle("курс usd/czk 25.000 источник синтетика", "owner")

    # RT20-01: STOP active -> quote commands (RE_GET / RE_GIVE) and Store.save_quote write 0 rows to quotes/events
    before_q = assistant.store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    before_ev = assistant.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    q_pre = build_quote(
        assistant.book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("1000"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW,
    )
    assistant.stop_file.write_text("stop", encoding="utf-8")
    try:
        ans_stop_buy = assistant.handle("купить 1000 usdt за czk при 3%", "owner")
        ans_stop_sell = assistant.handle("1000 usdt в czk при 3%", "owner")
        with pytest.raises(StoreError, match="СТОП"):
            assistant.store.save_quote(q_pre)
        after_q = assistant.store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        after_ev = assistant.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert "СТОП" in ans_stop_buy and "СТОП" in ans_stop_sell
        assert after_q == before_q and after_ev == before_ev
    finally:
        assistant.stop_file.unlink(missing_ok=True)

    # RT20-02: both buy and sell commands without explicit margin are refused and write 0 rows to quotes
    for cmd_no_margin in ("купить 1000 usdt за czk", "получить 10000 czk за usdt", "1000 usdt в czk"):
        ans = assistant.handle(cmd_no_margin, "owner")
        assert "Не выполнено" in ans and "явную ставку" in ans
        assert "наценка 3%" not in ans
    assert assistant.store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before_q

    # RT20-03: older manual rate never overwrites newer manual rate in RateBook.put or Store.manual_rates
    newer = Rate("USDCZK", Decimal("25"), "synthetic", NOW, "manual", NOW + timedelta(hours=12))
    older = Rate("USDCZK", Decimal("20"), "older-synthetic", NOW - timedelta(hours=1), "manual", NOW + timedelta(hours=12))
    rb = RateBook([newer])
    rb.put(older)
    assert rb.get("USDCZK", NOW).value == Decimal("25")
    assistant.store.set_manual_rate("EURCZK", Decimal("25.5"), "newer", NOW, "owner")
    assistant.store.set_manual_rate("EURCZK", Decimal("24.0"), "older", NOW - timedelta(hours=2), "owner")
    assert assistant.book().get("EURCZK", NOW).value == Decimal("25.5")

    # RT20-04: Store.confirm refuses settled deal without actual legs
    assistant.store.save_quote(q_pre)
    with pytest.raises(StoreError, match="фактические суммы обеих сторон"):
        assistant.store.confirm(q_pre.id, NOW, "owner", crm_status="settled")


def test_claude_audit_p1_items_5_to_11(assistant, tmp_path):
    """Claude Audit P1 Items 5..11 comprehensive regression test:
    5. confirm() only for 'quoted'; cancelled/draft refused; quote status updated in same transaction; accepted -> settled.
    6. Two Store instances on same DB: rate added in one is immediately visible in the second.
    7. margin=None -> margin_decision='default'; soft limit >10% asks for confirmation unless confirmed.
    8. STOP blocks quote save AND PDF write on disk.
    9. InvalidOperation caught cleanly; deal amounts outside ±10% refused without explicit confirmation.
    10. PDF encodes Cyrillic (cp1251/WinAnsiEncoding), re-issues VOID receipt on cancel, HTML escapes untrusted strings.
    11. Legacy DB migration: reversed -> cancelled backfill, duplicate column race handling, restore() runs migrations, verify() checks triggers.
    """
    from bossman_shared.exchange_assistant import render_branded_document

    # Item 6: Two Store instances on the same SQLite DB see manual rates immediately without stale cache
    store2 = Store(assistant.store.path)
    try:
        assistant.store.set_manual_rate("USDTUSD", Decimal("0.9995"), "store1-source", NOW, "owner")
        rates_seen_by_store2 = {r.pair: r.value for r in store2.manual_rates()}
        assert rates_seen_by_store2["USDTUSD"] == Decimal("0.9995")
    finally:
        store2.close()

    # Item 7: margin=None -> manager_margin_decision == "default"; >10% soft limit requires confirmation
    q_default = build_quote(book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("100"), now=NOW)
    assert q_default.manager_margin_decision == "default"
    with pytest.raises(QuoteError, match="превышает мягкий лимит 10%"):
        build_quote(
            book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("100"),
            margin=Decimal("0.15"), require_explicit_margin=True, now=NOW,
        )
    q_high_ok = build_quote(
        book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("100"),
        margin=Decimal("0.15"), require_explicit_margin=True, confirm_high_margin=True, now=NOW,
    )
    assert q_high_ok.margin == Decimal("0.15")

    assistant.handle("курс usd/czk 25.000 источник синтетика", "owner")
    assistant.handle("курс usdt/usd 1.0000 источник синтетика", "owner")
    ans_high = assistant.handle("1000 usdt в czk при 15%", "owner")
    assert "мягкий лимит 10%" in ans_high
    ans_high_ok = assistant.handle("1000 usdt в czk при 15% подтверждаю ставку", "owner")
    assert "Котировка" in ans_high_ok and "наценка 15%" in ans_high_ok

    # Item 5: cancelled quote cannot be confirmed
    q_canc = build_quote(
        assistant.book(), give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("200"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW + timedelta(seconds=10),
    )
    assistant.store.save_quote(q_canc, crm_status="quoted")
    assistant.store.cancel_quote(q_canc.id, "тест отмены", NOW + timedelta(seconds=11), "owner")
    with pytest.raises(StoreError, match="отменена"):
        assistant.store.confirm(
            q_canc.id, NOW + timedelta(seconds=12), "owner",
            leg_in_actual=Decimal("200"), leg_out_actual=Decimal("4850"),
        )

    # Item 9: InvalidOperation caught and ±10% deviation enforced
    q_norm_reply = assistant.handle("1000 usdt в czk при 3%", "owner")
    qid_norm = _qid(q_norm_reply)
    ans_dev = assistant.handle(f"обмен выполнен {qid_norm} приняли 1000 выдали 15000", "owner")
    assert "расхождение фактических сумм" in ans_dev and "±10%" in ans_dev and "подтверждаю расхождение" in ans_dev
    assert assistant.store.deals() == []
    # Confirming with explicit deviation override succeeds
    ans_dev_ok = assistant.handle(
        f"обмен выполнен {qid_norm} приняли 1000 выдали 24250", "owner"
    )
    assert "Сделка записана: D-1" in ans_dev_ok

    # Item 8: STOP blocks PDF disk write in deal_pdf and raises StoreError in Store._write_receipt_pdf
    pdf_file = assistant.store.receipt_pdf_path(1)
    pdf_file.unlink(missing_ok=True)
    assistant.stop_file.write_text("stop", encoding="utf-8")
    try:
        _, mem_pdf = assistant.deal_pdf(1)
        assert mem_pdf.startswith(b"%PDF-1.4\n")
        assert not pdf_file.exists()
        with pytest.raises(StoreError, match="СТОП"):
            assistant.store._write_receipt_pdf(assistant.store.deals()[0])
        assert not pdf_file.exists()
    finally:
        assistant.stop_file.unlink(missing_ok=True)

    # Item 10: PDF Cyrillic encoding (WinAnsiEncoding + cp1251 octal escapes), VOID receipt after cancel, html.escape
    _, pdf_bytes = assistant.deal_pdf(1)
    assert pdf_file.exists()
    assert b"/Encoding /WinAnsiEncoding" in pdf_bytes
    # "КВИТАНЦИЯ" in cp1251 starts with \xca\xc2\xc8 -> octal \312\302\310
    assert b"\\312\\302\\310" in pdf_bytes
    rev_reply = assistant.handle("отмена D-1 возврат клиенту", "owner")
    assert "отменена" in rev_reply
    void_bytes = pdf_file.read_bytes()
    assert b"VOID" in void_bytes

    xss_rate = Rate("USDCZK", Decimal("25.0"), "<script>alert(1)</script>", NOW, "manual", NOW + timedelta(hours=1))
    xss_book = RateBook([xss_rate, manual("USDTUSD", "1.0")])
    q_xss = build_quote(
        xss_book, give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("100"),
        margin=Decimal("0.03"), require_explicit_margin=True, now=NOW,
    )
    rendered_html = render_branded_document(q_xss, deal={"id": 99, "crm_status": "<b>settled</b>"})
    assert "<script>" not in rendered_html and "&lt;script&gt;" in rendered_html
    assert "<b>settled</b>" not in rendered_html and "<B>SETTLED</B>" not in rendered_html and "&lt;B&gt;SETTLED&lt;/B&gt;" in rendered_html

    # Item 11: Legacy v1 DB without new columns/triggers -> restore() runs SCHEMA + _migrate + backfills reversed->cancelled; verify() checks triggers
    legacy_db = tmp_path / "legacy_v1.db"
    con = sqlite3.connect(legacy_db)
    con.executescript(
        """
        CREATE TABLE events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE manual_rates(id INTEGER PRIMARY KEY, pair TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, set_at TEXT NOT NULL, expires_at TEXT NOT NULL, set_by TEXT NOT NULL);
        CREATE TABLE quotes(id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL, valid_until TEXT NOT NULL);
        CREATE TABLE deals(id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, confirmed_at TEXT NOT NULL, confirmed_by TEXT NOT NULL, give_ccy TEXT NOT NULL, give_amount TEXT NOT NULL, get_ccy TEXT NOT NULL, get_amount TEXT NOT NULL, client_rate TEXT NOT NULL, mid TEXT NOT NULL, reversed_at TEXT, reverse_reason TEXT);
        INSERT INTO quotes VALUES ('Q-LEGACY01', '{"id":"Q-LEGACY01","give_ccy":"USDT","give_amount":"100","get_ccy":"CZK","get_amount":"2425","mid":"25","client_rate":"24.25","margin":"0.03","created_at":"2026-10-19T10:00:00+00:00","valid_until":"2026-10-19T10:15:00+00:00","rates":[],"formula":"f","fixed_side":"give"}', '2026-10-19T10:00:00+00:00', '2026-10-19T10:15:00+00:00');
        INSERT INTO deals(id, quote_id, status, confirmed_at, confirmed_by, give_ccy, give_amount, get_ccy, get_amount, client_rate, mid, reversed_at, reverse_reason)
        VALUES (1, 'Q-LEGACY01', 'reversed', '2026-10-19T10:01:00+00:00', 'owner', 'USDT', '100', 'CZK', '2425', '24.25', '25', '2026-10-19T10:05:00+00:00', 'legacy cancel');
        """
    )
    con.commit()
    con.close()

    # Without triggers, Store.verify refuses legacy_db directly
    with pytest.raises(StoreError, match="deals_no_delete"):
        Store.verify(legacy_db)

    # Opening Store on legacy_db migrates columns, installs triggers, and backfills reversed -> cancelled
    migrated_store = Store(legacy_db)
    try:
        Store.verify(legacy_db)
        d_legacy = migrated_store.deals()[0]
        assert d_legacy["status"] == "reversed"
        assert d_legacy["crm_status"] == "cancelled"
    finally:
        migrated_store.close()


@pytest.mark.parametrize("pct", ["10", "3.5"])
def test_round_margin_is_shown_as_plain_percent_not_scientific(assistant, pct):
    """Decimal('10.0').normalize() is Decimal('1E+1'): the owner must read «наценка 10%», never «1E+1%»."""
    from bossman_shared.exchange_assistant import render_branded_document
    from bossman_shared.exchange_assistant.quote import render_pdf_document
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    out = assistant.handle(f"500 usdt в czk при {pct}%", "owner")
    assert f"наценка {pct}%" in out
    assert "E+" not in out
    q = assistant.store.quote_object(_qid(out))
    assert f"{pct}%" in render_branded_document(q) and "E+" not in render_branded_document(q)
    assert f"{pct}%".encode() in render_pdf_document(q) and b"E+" not in render_pdf_document(q)


@pytest.mark.parametrize("pct", ["20", "40"])
def test_soft_limit_refusal_names_round_margin_plainly(assistant, pct):
    """The >10% soft-limit question must say «ставка 20%», not «ставка 2E+1%»; confirmed it quotes «наценка 20%»."""
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    refused = assistant.handle(f"500 usdt в czk при {pct}%", "owner")
    assert f"ставка {pct}% превышает мягкий лимит" in refused and "E+" not in refused
    ok = assistant.handle(f"500 usdt в czk при {pct}% подтверждаю ставку", "owner")
    assert f"наценка {pct}%" in ok and "E+" not in ok


def test_today_dashboard_is_the_prague_day_not_the_utc_day(assistant):
    """At 00:30 Prague (22:30 UTC) «итоги сегодня» is the new Prague day: a deal at 23:30 Prague yesterday is out."""
    clock = assistant.clock_ref
    for t in (datetime(2026, 10, 19, 21, 30, tzinfo=timezone.utc),     # 23:30 Prague, 19.10
              datetime(2026, 10, 19, 22, 10, tzinfo=timezone.utc)):    # 00:10 Prague, 20.10
        clock["now"] = t
        assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
        qid = _qid(assistant.handle("100 usdt в czk при 3%", "owner"))
        assert "Сделка записана" in assistant.handle(f"обмен выполнен {qid} приняли 100 выдали 2093,10", "owner")
    clock["now"] = datetime(2026, 10, 19, 22, 30, tzinfo=timezone.utc)
    out = assistant.handle("итоги сегодня", "owner")
    assert "Итоги: 20.10.2026 — 20.10.2026" in out
    assert "Сделок: 1 " in out
