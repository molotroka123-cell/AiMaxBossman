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
    assert "USDT/USD" in assistant.handle("500 usdt в czk", "owner")        # no USDT/USD yet -> refused
    assert "Записан ручной курс" in assistant.handle("курс usdt/usd 0,999 источник биржа binance", "owner")
    q = assistant.handle("500 usdt в czk", "owner")
    assert "10465.52 CZK" in q and "ЧНБ" in q and "наценка 3%" in q
    qid = _qid(q)
    assert "Сделка записана: D-1" in assistant.handle(f"обмен выполнен {qid}", "owner")
    assert "повтор не создан" in assistant.handle(f"обмен выполнен {qid}", "owner")
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
    qid = _qid(assistant.handle("получить 10000 czk за usdt", "owner"))
    assistant.clock_ref["now"] = NOW + timedelta(minutes=16)
    assert "просрочена" in assistant.handle(f"обмен выполнен {qid}", "owner")
    assert assistant.store.deals() == []


def test_non_owner_gets_nothing(assistant):
    for cmd in ("курс", "500 usdt в czk", "итоги", "курс usdt/usd 1 источник x", "обмен выполнен Q-00000000"):
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
    assistant.handle(f"обмен выполнен {_qid(assistant.handle('100 usdt в eur', 'owner'))}", "owner")
    with pytest.raises(sqlite3.DatabaseError):
        assistant.store.db.execute("DELETE FROM deals")
    with pytest.raises(sqlite3.DatabaseError):
        assistant.store.db.execute("UPDATE events SET kind='x'")


def test_backup_and_restore_roundtrip(assistant, tmp_path):
    assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
    assistant.handle(f"обмен выполнен {_qid(assistant.handle('100 usdt в eur', 'owner'))}", "owner")
    copy = assistant.store.backup(tmp_path / "bk" / "copy.db")
    Store.verify(copy)
    assistant.handle(f"обмен выполнен {_qid(assistant.handle('200 usdt в eur', 'owner'))}", "owner")
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
    qid = _qid(assistant.handle("100 usdt в czk", "owner"))
    assistant.stop_file.write_text("stop")
    assert "СТОП" in assistant.handle(f"обмен выполнен {qid}", "owner")
    assert "СТОП" in assistant.handle("отмена D-1 x", "owner")
    assert "Курсы" in assistant.handle("курс", "owner")             # reading still works
    assert assistant.store.deals() == []
    assistant.stop_file.unlink()
    assert "Сделка записана" in assistant.handle(f"обмен выполнен {qid}", "owner")


def test_two_surfaces_share_one_book(assistant, tmp_path):
    """Pult and phone app open their own Assistant on the same file: a quote made in one is confirmed in the other."""
    other = Assistant(Store(assistant.store.path), fx_db=assistant.fx_db, clock=assistant.clock, stop_file=assistant.stop_file)
    try:
        assistant.handle("курс usdt/usd 0.999 источник тест", "owner")
        qid = _qid(other.handle("100 usdt в czk", "owner"))
        assert "Сделка записана: D-1" in assistant.handle(f"обмен выполнен {qid}", "owner")
        assert "повтор не создан" in other.handle(f"обмен выполнен {qid}", "owner")
        assert len(other.store.deals()) == 1
    finally:
        other.store.close()


def test_business_month_21_to_20():
    s, e = business_month(datetime(2026, 10, 19, 12, tzinfo=timezone.utc))
    assert (s.month, s.day, e.month, e.day) == (9, 21, 10, 21)
    s, e = business_month(datetime(2026, 12, 25, 12, tzinfo=timezone.utc))
    assert (s.year, s.month, e.year, e.month) == (2026, 12, 2027, 1)
