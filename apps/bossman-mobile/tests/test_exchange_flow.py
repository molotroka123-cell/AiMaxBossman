"""«Обмен»: the SwapMe practical flow over the phone API (owner item 7, 01.11). Session + CSRF, STOP, refusals, statement.
Synthetic data only; the rate book is a fixed in-memory CNB rate (no network)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bossman_shared.exchange_assistant import Assistant, Rate, RateBook
from bossman_shared.exchange_assistant.store import Store as Book

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def _assistant(tmp_path, clock):
    book = Book(tmp_path / "assistant.db")
    a = Assistant(book, fx_db=tmp_path / "none.db", clock=lambda: clock["now"], stop_file=tmp_path / "STOP")
    a.book = lambda: RateBook([Rate("USDCZK", Decimal("25.00"), "ЧНБ", NOW - timedelta(hours=2), "official",
                                    rate_date="2026-10-30")])
    return a


DEAL = {"client": "anna", "side": "client_buys", "base": "USDT", "amount": "1000", "markup_pct": "3",
        "assume_usdt_usd_parity": True, "training": True}


def test_flow_needs_login_and_csrf(env, tmp_path):
    env.app.state.exchange = _assistant(tmp_path, {"now": NOW})
    assert env.c.post("/m/api/exchange/flow/quote", json=DEAL).status_code == 401
    assert env.c.get("/m/api/exchange/statement").status_code == 401
    env.login()
    r = env.c.post("/m/api/exchange/flow/quote", json=DEAL)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"


def test_quote_pending_confirm_statement_over_the_api(authed, tmp_path):
    clock = {"now": NOW}
    authed.app.state.exchange = _assistant(tmp_path, clock)
    r = authed.post("/m/api/exchange/flow/quote", json=DEAL)
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    assert rec["status"] == "quote" and rec["counts_as_revenue"] is False and rec["training"] is True
    assert rec["client_gives"]["quoted"] == "25750.00" and rec["rate_source"] == "ЧНБ USD/CZK"
    assert rec["rate_date"] == "2026-10-30" and rec["assumptions"]
    qid = rec["quote_id"]
    clock["now"] = NOW + timedelta(minutes=3)
    pend = authed.post(f"/m/api/exchange/flow/{qid}/accept").json()["record"]
    assert pend["status"] == "pending" and pend["counts_as_revenue"] is False
    clock["now"] = NOW + timedelta(hours=1)
    off = authed.post(f"/m/api/exchange/flow/{qid}/confirm", json={"actual_in": "25000", "actual_out": "1000"})
    assert off.status_code == 422 and off.json()["detail"]["code"] == "BM_EXCHANGE_REFUSED"
    assert "больше допуска" in off.json()["detail"]["message"]
    done = authed.post(f"/m/api/exchange/flow/{qid}/confirm", json={"actual_in": "25750.00", "actual_out": 1000})
    assert done.status_code == 200 and done.json()["record"]["status"] == "confirmed"
    assert authed.c.get(f"/m/api/exchange/flow/{qid}").json()["record"]["client_gets"]["actual"] == "1000.00"
    st = authed.c.get("/m/api/exchange/statement", params={"client": "anna"}).json()
    assert st["counts"]["confirmed"] == 1 and st["confirmed_totals"]["CZK"]["received"] == "25750.00"
    csv_r = authed.c.get("/m/api/exchange/statement", params={"client": "anna", "format": "csv",
                                                              "start": "2026-11-01", "end": "2026-11-01"})
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
    assert 'filename="swapme-statement-anna.csv"' in csv_r.headers["content-disposition"]
    rows = [x for x in csv.DictReader(io.StringIO(csv_r.content.decode("utf-8-sig"))) if x["quote_id"]]
    assert len(rows) == 1 and rows[0]["status"] == "confirmed" and rows[0]["client_gives_actual"] == "25750.00"
    empty = authed.c.get("/m/api/exchange/statement", params={"start": "2026-10-01", "end": "2026-10-31"}).json()
    assert empty["rows"] == []
    txt = authed.c.get("/m/api/exchange/statement", params={"format": "text"})
    assert txt.status_code == 200 and "не выручка" in txt.text and "УЧЕБНАЯ" in txt.text
    # the same record is in the deal list of the «Обмен» tab
    assert [d["quote_id"] for d in authed.c.get("/m/api/exchange/deals").json()["deals"]] == [qid]


def test_bad_data_is_refused_with_a_message_and_nothing_is_written(authed, tmp_path):
    a = authed.app.state.exchange = _assistant(tmp_path, {"now": NOW})
    for patch, needle in (({"amount": "-5"}, "сумма"), ({"amount": "NaN"}, "сумма"), ({"amount": "abc"}, "сумма"),
                          ({"amount": 0}, "сумма"), ({"markup_pct": None}, "наценка не указана"),
                          ({"markup_pct": "11"}, "вне допустимого"), ({"assume_usdt_usd_parity": False}, "USDT/USD"),
                          ({"base": "BTC"}, "не поддерживается"), ({"client": "+420777123456"}, "клиент")):
        r = authed.post("/m/api/exchange/flow/quote", json={**DEAL, **patch})
        assert r.status_code == 422, (patch, r.text)
        assert needle in r.json()["detail"]["message"], (patch, r.json())
    stale = authed.post("/m/api/exchange/flow/quote", json={**DEAL, "max_rate_age_hours": 1})
    assert stale.status_code == 422 and "устарел" in stale.json()["detail"]["message"]
    assert a.store.crm_rows() == []
    bad_dates = authed.c.get("/m/api/exchange/statement", params={"start": "01.11.2026"})
    assert bad_dates.status_code == 422


def test_stop_blocks_flow_writes_but_not_the_statement(authed, tmp_path):
    a = authed.app.state.exchange = _assistant(tmp_path, {"now": NOW})
    qid = authed.post("/m/api/exchange/flow/quote", json=DEAL).json()["record"]["quote_id"]
    a.stop_file.write_text("stop")
    r = authed.post("/m/api/exchange/flow/quote", json={**DEAL, "client": "bob"})
    assert r.status_code == 423 and r.json()["detail"]["code"] == "BM_STOP"
    r = authed.post(f"/m/api/exchange/flow/{qid}/confirm", json={"actual_in": "25750", "actual_out": "1000"})
    assert r.status_code == 423
    assert a.store.deals() == []
    assert authed.c.get("/m/api/exchange/statement").status_code == 200


def test_the_tab_text_route_runs_the_same_flow(authed, tmp_path):
    authed.app.state.exchange = _assistant(tmp_path, {"now": NOW})
    r = authed.post("/m/api/exchange", json={"text": "сделка anna купить 1000 usdt за czk наценка 3% usdt=usd учебная"}).json()
    assert r["ok"] and r["quote_id"] and "25750.00 CZK" in r["reply"]
    assert authed.post("/m/api/exchange", json={"text": f"клиент согласен {r['quote_id']}"}).json()["ok"]
    done = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {r['quote_id']} приняли 25750 выдали 1000"}).json()
    assert done["ok"] and "D-1" in done["reply"]
    st = authed.post("/m/api/exchange", json={"text": "выписка anna все"}).json()
    assert st["ok"] and "подтверждена фактическими суммами" in st["reply"]
