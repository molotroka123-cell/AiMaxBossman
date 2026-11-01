"""SwapMe practical flow (owner item 7, 01.11): sourced rate -> explicit markup -> Decimal ROUND_HALF_UP -> quote ->
pending -> confirmed ACTUAL amounts -> local CRM -> statement. Synthetic data only; no network (fake fetcher)."""
from __future__ import annotations

import csv
import io
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "war-terminal"))   # this tree's War Terminal, never a foreign editable copy

from bossman_shared.exchange_assistant import Assistant, Rate, RateBook  # noqa: E402
from bossman_shared.exchange_assistant import flow  # noqa: E402
from bossman_shared.exchange_assistant.rates import load_official  # noqa: E402
from bossman_shared.exchange_assistant.store import Store, StoreError  # noqa: E402

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
FIX = ROOT / "apps" / "war-terminal" / "tests" / "fixtures_fx"


def cnb(value="25.00", at=NOW - timedelta(hours=2), src="ЧНБ", rate_date="2026-10-30"):
    return Rate("USDCZK", Decimal(value), src, at, "official", rate_date=rate_date)


def book(*rates):
    return RateBook(list(rates) or [cnb(), Rate("EURCZK", Decimal("24.325"), "ЧНБ", NOW - timedelta(hours=2), "official",
                                                rate_date="2026-10-30")])


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "assistant.db")
    yield s
    s.close()


def q(store, b=None, **kw):
    args = dict(client="anna", side="client_buys", base="USDT", amount="1000", markup_pct="3", now=NOW,
                assume_usdt_usd_parity=True)
    args.update(kw)
    return flow.quote(store, b or book(), **args)


# ------------------------------------------------------------------ 3. Decimal maths and rounding
def test_owner_example_1000_usdt_x_25_x_1_03_is_25750_00_czk(store):
    rate, total = flow.manager_total(Decimal("1000"), Decimal("25.00"), Decimal("3"), "client_buys")
    assert total == Decimal("25750.00") and isinstance(total, Decimal) and str(total) == "25750.00"
    assert rate == Decimal("25.75")
    rec = q(store)
    assert rec["client_gives"] == {"ccy": "CZK", "quoted": "25750.00", "actual": None}
    assert rec["client_gets"] == {"ccy": "USDT", "quoted": "1000.00", "actual": None}
    assert "1000.00 USDT × [USD/CZK 25.00 (ЧНБ) × USDT/USD 1 (допущение)] × (1 + 3%) = 25750.00 CZK" in rec["formula"]


def test_rounding_is_round_half_up_to_cent_and_the_test_can_tell_it_from_bankers():
    _, half = flow.manager_total(Decimal("1"), Decimal("10.005"), Decimal("0"), "client_buys")
    assert half == Decimal("10.01")
    # negative control: banker's rounding (the Decimal default) would give 10.00 — the test above would catch it
    assert Decimal("10.005").quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) == Decimal("10.00")
    _, below = flow.manager_total(Decimal("1"), Decimal("10.0049"), Decimal("0"), "client_buys")
    assert below == Decimal("10.00")
    # client sells: 500 × 21.622 × (1 − 2.5%) = 10540.725 -> 10540.73 (one rounding, at the end)
    _, sells = flow.manager_total(Decimal("500"), Decimal("21.622"), Decimal("2.5"), "client_sells")
    assert sells == (Decimal("500") * Decimal("21.622") * Decimal("0.975")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert sells == Decimal("10540.73")
    assert flow.ROUNDING is ROUND_HALF_UP


def test_float_input_goes_through_its_string_not_binary_arithmetic():
    assert flow.dec(0.1, "x") == Decimal("0.1")              # Decimal(0.1) would be 0.1000000000000000055511...
    assert flow.dec("1 000,50", "x") == Decimal("1000.50")


# ------------------------------------------------------------------ 1. rate source with its date (fake fetcher, no network)
def test_rate_comes_from_the_existing_war_terminal_collector_with_source_and_dates(tmp_path, store):
    calls = []

    def fake_get(url):
        calls.append(url)
        if "cnb.cz" in url:
            return (FIX / "cnb_daily.txt").read_bytes()
        if "ecb.europa.eu" in url:
            return (FIX / "ecb_daily.xml").read_bytes()
        raise AssertionError("tests never reach the network: " + url)

    observed = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
    res = flow.collect_official_rates(tmp_path / "fx", fake_get, now=observed, sleep=lambda s: None)
    assert res["status"] == "OK" and res["written"] >= 3
    assert [u.split("/")[2] for u in calls] == ["www.cnb.cz", "www.ecb.europa.eu"]
    rates = {r.pair: r for r in load_official(tmp_path / "fx" / "fx.db")}
    usd = rates["USDCZK"]
    assert usd.source == "ЧНБ" and usd.rate_date == "2026-10-16" and usd.as_of == observed
    assert usd.value == Decimal("21.622")
    rec = q(store, RateBook(list(rates.values())), now=observed + timedelta(hours=1))
    assert rec["rate_source"] == "ЧНБ USD/CZK" and rec["rate_as_of"] == observed.isoformat()
    assert rec["rate_date"] == "2026-10-16" and rec["market_rate"] == "21.622"
    assert rec["client_gives"]["quoted"] == "22270.66"       # 1000 × 21.622 × 1.03 = 22270.66 exactly


# ------------------------------------------------------------------ 2. explicit markup per deal, no hidden default
@pytest.mark.parametrize("markup", [None, "", "   "])
def test_markup_must_be_typed_for_every_deal(store, markup):
    with pytest.raises(flow.FlowError, match="наценка не указана"):
        q(store, markup_pct=markup)
    assert store.crm_rows() == []


@pytest.mark.parametrize("markup", ["-0.5", "10.01", "50", "NaN", "abc"])
def test_markup_outside_the_allowed_range_or_not_a_number_is_refused(store, markup):
    with pytest.raises(flow.FlowError, match="наценка"):
        q(store, markup_pct=markup)
    assert store.crm_rows() == []


def test_markup_bounds_are_inclusive(store):
    assert q(store, markup_pct="0")["client_gives"]["quoted"] == "25000.00"
    assert q(store, markup_pct="10", client="bob")["client_gives"]["quoted"] == "27500.00"


# ------------------------------------------------------------------ USDT ≈ USD is an explicit, recorded assumption
def test_usdt_without_a_usdt_rate_and_without_the_assumption_is_refused(store):
    with pytest.raises(flow.FlowError, match="USDT/USD"):
        q(store, assume_usdt_usd_parity=False)


def test_usdt_parity_assumption_is_written_into_the_record_and_statement(store):
    rec = q(store)
    assert rec["assumptions"] == [flow.USDT_PARITY_NOTE] and "ДОПУЩЕНИЕ" in rec["assumptions"][0]
    assert "ДОПУЩЕНИЕ" in flow.statement_text(flow.statement(store, NOW))


def test_an_owner_usdt_rate_with_source_beats_the_assumption(store):
    b = book(cnb(), Rate("USDTUSD", Decimal("0.999"), "binance (ручной)", NOW - timedelta(minutes=5), "manual",
                         NOW + timedelta(hours=1)))
    rec = q(store, b)
    assert rec["assumptions"] == [] and Decimal(rec["market_rate"]) == Decimal("24.975")
    assert "binance" in rec["rate_source"]
    assert rec["client_gives"]["quoted"] == "25724.25"       # 1000 × 24.975 × 1.03 = 25724.25


# ------------------------------------------------------------------ 6. bad data
@pytest.mark.parametrize("amount", ["-1000", "0", "0.00", "NaN", "nan", float("nan"), "inf", float("inf"), "abc", "",
                                    "12.345", None, True])
def test_bad_amounts_are_refused_with_a_reason(store, amount):
    with pytest.raises(flow.FlowError) as e:
        q(store, amount=amount)
    assert str(e.value).startswith("сумма")
    assert store.crm_rows() == []


def test_a_rate_older_than_n_hours_is_refused(store):
    old = cnb(at=NOW - timedelta(hours=5), rate_date=None)
    with pytest.raises(flow.FlowError, match="устарел.*5 ч > 4 ч"):
        q(store, book(old), max_rate_age_hours=4)
    assert q(store, book(old), max_rate_age_hours=6)["status"] == "quote"     # the same rate inside N is fine


def test_a_freshly_fetched_but_old_fixing_is_stale_too(store):
    """observed now, but the source published it for a date long ago: the fixing date decides."""
    r = cnb(at=NOW - timedelta(minutes=10), rate_date="2026-10-20")
    with pytest.raises(flow.FlowError, match="фиксинг на 20.10.2026"):
        q(store, book(r))


def test_a_rate_beyond_the_hard_96h_ceiling_is_refused_by_the_book_itself(store):
    with pytest.raises(flow.FlowError, match="устарел"):
        q(store, book(cnb(at=NOW - timedelta(hours=97), rate_date=None)))
    with pytest.raises(flow.FlowError, match="от 1 до 96"):
        q(store, max_rate_age_hours=200)


@pytest.mark.parametrize("src", ["", "   "])
def test_a_rate_without_a_source_is_refused(store, src):
    with pytest.raises(flow.FlowError, match="нет источника"):
        q(store, book(cnb(src=src)))


def test_unsupported_pairs_and_sides_are_refused(store):
    with pytest.raises(flow.FlowError, match="не поддерживается"):
        q(store, base="BTC")
    with pytest.raises(flow.FlowError, match="сторона сделки"):
        q(store, side="buy_everything")


@pytest.mark.parametrize("alias", ["", " ", "=HYPERLINK(1)", "+420777123456", "a" * 41])
def test_client_alias_is_a_short_pseudonym_not_personal_data_or_a_formula(store, alias):
    with pytest.raises(flow.FlowError, match="клиент"):
        q(store, client=alias)


# ------------------------------------------------------------------ 4. confirmation of ACTUAL amounts
def test_status_is_quote_then_pending_then_confirmed_and_never_revenue_before(store):
    rec = q(store)
    assert rec["status"] == "quote" and rec["counts_as_revenue"] is False and rec["deal_id"] is None
    pend = flow.accept(store, rec["quote_id"], NOW + timedelta(minutes=5))
    assert pend["status"] == "pending" and pend["counts_as_revenue"] is False and pend["deal_id"] == 1
    assert pend["client_gives"]["actual"] is None
    stmt = flow.statement(store, NOW + timedelta(hours=1))
    assert stmt["confirmed_totals"] == {} and stmt["counts"]["pending"] == 1
    # money arrives two hours later — after the 15-minute quote validity, the pending deal can still be confirmed
    done = flow.confirm(store, rec["quote_id"], NOW + timedelta(hours=2), actual_in="25750.00", actual_out="1000")
    assert done["status"] == "confirmed" and done["client_gives"]["actual"] == "25750.00"
    assert done["client_gets"]["actual"] == "1000.00" and done["markup_value"]["amount"] == "750.00"
    assert done["counts_as_revenue"] is True


def test_a_training_deal_never_counts_as_revenue(store):
    rec = q(store, training=True)
    done = flow.confirm(store, rec["quote_id"], NOW + timedelta(minutes=1), actual_in="25750", actual_out="1000")
    assert done["status"] == "confirmed" and done["training"] is True and done["counts_as_revenue"] is False


def test_confirmation_beyond_tolerance_is_refused_and_nothing_is_recorded(store):
    rec = q(store)                                        # tolerance 0.5 %: 25750 ± 128.75
    with pytest.raises(flow.FlowError, match="расходятся с котировкой.*0.5%"):
        flow.confirm(store, rec["quote_id"], NOW, actual_in="25600", actual_out="1000")
    with pytest.raises(flow.FlowError, match="выдано 1010.00 USDT"):
        flow.confirm(store, rec["quote_id"], NOW, actual_in="25750", actual_out="1010")
    assert store.deals() == [] and flow.record(store, rec["quote_id"], NOW)["status"] == "quote"
    ok = flow.confirm(store, rec["quote_id"], NOW, actual_in="25700.00", actual_out="1000")   # 0.19 %: inside
    assert ok["status"] == "confirmed" and ok["client_gives"]["actual"] == "25700.00"


@pytest.mark.parametrize("bad", ["-1", "0", "NaN", "x", "", None, "25750.001"])
def test_bad_actual_amounts_are_refused(store, bad):
    rec = q(store)
    with pytest.raises(flow.FlowError, match="фактически"):
        flow.confirm(store, rec["quote_id"], NOW, actual_in=bad, actual_out="1000")
    assert store.deals() == []


def test_an_expired_quote_without_client_agreement_cannot_be_confirmed(store):
    rec = q(store)
    with pytest.raises(StoreError, match="просрочена"):
        flow.confirm(store, rec["quote_id"], NOW + timedelta(minutes=16), actual_in="25750", actual_out="1000")
    assert flow.record(store, rec["quote_id"], NOW + timedelta(minutes=16))["status"] == "expired"


def test_reconfirming_with_other_amounts_is_refused_same_amounts_is_idempotent(store):
    rec = q(store)
    flow.confirm(store, rec["quote_id"], NOW, actual_in="25750", actual_out="1000")
    again = flow.confirm(store, rec["quote_id"], NOW, actual_in="25750.00", actual_out="1000.00")
    assert again["deal_id"] == 1 and len(store.deals()) == 1
    with pytest.raises(flow.FlowError, match="уже подтверждены другие суммы"):
        flow.confirm(store, rec["quote_id"], NOW, actual_in="25740", actual_out="1000")


# ------------------------------------------------------------------ 5. CRM record + statement
def test_crm_rows_are_append_only(store):
    rec = q(store)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("UPDATE crm_records SET client_alias='x' WHERE quote_id=?", (rec["quote_id"],))
    with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
        store.db.execute("DELETE FROM crm_records")


def test_statement_per_client_and_period_csv_and_text(store):
    a = q(store, client="anna")
    flow.confirm(store, a["quote_id"], NOW, actual_in="25750", actual_out="1000")
    b = q(store, client="bob", side="client_sells", base="EUR", amount="200", markup_pct="2", now=NOW + timedelta(days=2))
    assert b["client_gets"]["quoted"] == "4767.70"         # 200 × 24.325 × 0.98 = 4767.70
    later = NOW + timedelta(days=2, minutes=1)
    only_anna = flow.statement(store, later, client="anna")
    assert [r["client"] for r in only_anna["rows"]] == ["anna"]
    assert only_anna["confirmed_totals"] == {"CZK": {"received": "25750.00", "paid": "0"},
                                             "USDT": {"received": "0", "paid": "1000.00"}}
    period = flow.statement(store, later, start=NOW + timedelta(days=1), end=NOW + timedelta(days=3))
    assert [r["client"] for r in period["rows"]] == ["bob"] and period["confirmed_totals"] == {}
    assert period["counts"]["quote"] == 1
    rows = list(csv.DictReader(io.StringIO(flow.statement_csv(flow.statement(store, later)))))
    data = [r for r in rows if r["created_at"] and not r["created_at"].startswith("#")]
    assert [(r["client"], r["status"]) for r in data] == [("anna", "confirmed"), ("bob", "quote")]
    assert data[0]["rate_source"] == "ЧНБ USD/CZK" and data[0]["rate_date"] == "2026-10-30" and data[0]["markup_pct"] == "3"
    assert data[1]["client_gets_actual"] == ""             # a quote has no actual amounts
    text = flow.statement_text(flow.statement(store, later))
    assert "не выручка" in text and "anna" in text and "bob" in text and "котировка (не сделка, не выручка)" in text


# ------------------------------------------------------------------ the same flow through the shared assistant (pult / «Обмен»)
@pytest.fixture()
def assistant(tmp_path, store):
    clock = {"now": NOW}
    a = Assistant(store, fx_db=tmp_path / "none.db", clock=lambda: clock["now"], stop_file=tmp_path / "STOP")
    a.book = lambda: book()
    a.clock_ref = clock
    return a


def test_text_commands_run_the_whole_flow(assistant):
    r = assistant.handle("сделка anna купить 1000 usdt за czk наценка 3% usdt=usd учебная", "owner")
    assert r.startswith("Котировка Q-") and "Клиент отдаёт: 25750.00 CZK" in r and "УЧЕБНАЯ" in r and "ДОПУЩЕНИЕ" in r
    qid = r.split()[1]
    assert "наценка не указана" not in r
    no_markup = assistant.handle("сделка anna купить 1000 usdt за czk", "owner")
    assert no_markup.startswith("Не понял") or no_markup.startswith("Не выполнено")
    pending = assistant.handle(f"клиент согласен {qid}", "owner")
    assert "ждём деньги" in pending and "D-1" in pending
    no_amounts = assistant.handle(f"обмен выполнен {qid}", "owner")
    assert no_amounts.startswith("Не выполнено") and "фактические суммы" in no_amounts
    override = assistant.handle(f"обмен выполнен {qid} приняли 20000 выдали 1000 подтверждаю расхождение", "owner")
    assert override.startswith("Не выполнено") and "не действует" in override
    off = assistant.handle(f"обмен выполнен {qid} приняли 20000 выдали 1000", "owner")
    assert off.startswith("Не выполнено") and "больше допуска" in off
    done = assistant.handle(f"обмен выполнен {qid} приняли 25750 выдали 1000", "owner")
    assert done.startswith("Сделка записана: D-1") and "не выручка" in done
    st = assistant.handle("выписка anna все", "owner")
    assert "подтверждена фактическими суммами [УЧЕБНАЯ]" in st and "получено 25750.00 CZK" in st


def test_markup_out_of_range_and_stop_through_the_assistant(assistant, tmp_path):
    r = assistant.handle("сделка anna купить 1000 usdt за czk наценка 12% usdt=usd", "owner")
    assert r.startswith("Не выполнено") and "вне допустимого диапазона" in r
    (tmp_path / "STOP").write_text("stop")
    r = assistant.handle("сделка anna купить 1000 usdt за czk наценка 3% usdt=usd", "owner")
    assert "СТОП" in r and assistant.store.crm_rows() == []
    with pytest.raises(StoreError, match="СТОП"):
        assistant.flow_quote(client="anna", side="client_buys", base="USDT", amount="1", markup_pct="1",
                             assume_usdt_usd_parity=True)
    assert "Записей нет" in assistant.handle("выписка", "owner")      # reading still works under STOP


def test_not_the_owner_gets_nothing(assistant):
    assert assistant.handle("выписка", "someone") == "Помощник обменника доступен только владельцу."
