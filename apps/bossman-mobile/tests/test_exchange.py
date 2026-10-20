"""«Обменник» in the phone app: session + CSRF, same assistant as the pult, STOP blocks writes (owner 19.10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bossman_shared.exchange_assistant import Assistant, Rate, RateBook
from bossman_shared.exchange_assistant.store import Store as Book

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)


def _assistant(tmp_path):
    book = Book(tmp_path / "assistant.db")
    a = Assistant(book, fx_db=tmp_path / "none.db", clock=lambda: NOW, stop_file=tmp_path / "STOP")

    def rates():
        b = RateBook([Rate("USDCZK", Decimal("21.6"), "ЧНБ", NOW - timedelta(hours=1), "official")])
        for r in book.manual_rates():
            b.put(r)
        return b
    a.book = rates
    return a


def test_exchange_needs_login_and_csrf(env, tmp_path):
    env.app.state.exchange = _assistant(tmp_path)
    assert env.c.post("/m/api/exchange", json={"text": "курс"}).status_code == 401
    env.login()
    r = env.c.post("/m/api/exchange", json={"text": "курс"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"


def test_quote_confirm_deals_and_duplicate(authed, tmp_path):
    authed.app.state.exchange = _assistant(tmp_path)
    assert authed.post("/m/api/exchange", json={"text": "курс usdt/usd 0.999 источник binance"}).json()["ok"]
    no_margin = authed.post("/m/api/exchange", json={"text": "500 usdt в czk"}).json()
    assert not no_margin["ok"] and "явную ставку" in no_margin["reply"]
    r = authed.post("/m/api/exchange", json={"text": "500 usdt в czk при 3%"}).json()
    assert r["ok"] and r["quote_id"].startswith("Q-") and "10465.52 CZK" in r["reply"]
    no_legs = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {r['quote_id']}"}).json()
    assert not no_legs["ok"] and "фактические суммы обеих сторон" in no_legs["reply"]
    done = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {r['quote_id']} приняли 500 выдали 10465.52"}).json()
    assert done["ok"] and "D-1" in done["reply"]
    again = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {r['quote_id']} приняли 500 выдали 10465.52"}).json()
    assert "повтор не создан" in again["reply"]
    deals = authed.c.get("/m/api/exchange/deals").json()["deals"]
    assert len(deals) == 1 and deals[0]["quote_id"] == r["quote_id"] and deals[0]["get_amount"] == "10465.52"
    assert deals[0]["pdf_url"] == "/m/api/exchange/deals/1/pdf"
    pdf_resp = authed.c.get("/m/api/exchange/deals/1/pdf")
    assert pdf_resp.status_code == 200 and pdf_resp.headers["content-type"].startswith("application/pdf")
    assert pdf_resp.content.startswith(b"%PDF-1.4\n") and b"SwapMe Exchange" in pdf_resp.content


def test_stop_blocks_writes_in_the_app(authed, tmp_path):
    a = authed.app.state.exchange = _assistant(tmp_path)
    authed.post("/m/api/exchange", json={"text": "курс usdt/usd 0.999 источник binance"})
    qid = authed.post("/m/api/exchange", json={"text": "100 usdt в czk при 3%"}).json()["quote_id"]
    a.stop_file.write_text("stop")
    r_q = authed.post("/m/api/exchange", json={"text": "100 usdt в czk при 3%"}).json()
    assert not r_q["ok"] and r_q["stopped"] and "СТОП" in r_q["reply"]
    r = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {qid} приняли 100 выдали 2093.10"}).json()
    assert not r["ok"] and r["stopped"] and "СТОП" in r["reply"]
    assert authed.c.get("/m/api/exchange/deals").json()["deals"] == []


def test_input_limits(authed, tmp_path):
    authed.app.state.exchange = _assistant(tmp_path)
    assert authed.post("/m/api/exchange", json={"text": ""}).status_code == 422
    assert authed.post("/m/api/exchange", json={"text": "x" * 301}).status_code == 422
    r = authed.post("/m/api/exchange", json={"text": "сделай что-нибудь"}).json()
    assert "Не понял" in r["reply"]
