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
    r = authed.post("/m/api/exchange", json={"text": "500 usdt в czk"}).json()
    assert r["ok"] and r["quote_id"].startswith("Q-") and "10465.52 CZK" in r["reply"]
    done = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {r['quote_id']}"}).json()
    assert done["ok"] and "D-1" in done["reply"]
    again = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {r['quote_id']}"}).json()
    assert "повтор не создан" in again["reply"]
    deals = authed.c.get("/m/api/exchange/deals").json()["deals"]
    assert len(deals) == 1 and deals[0]["quote_id"] == r["quote_id"] and deals[0]["get_amount"] == "10465.52"


def test_stop_blocks_writes_in_the_app(authed, tmp_path):
    a = authed.app.state.exchange = _assistant(tmp_path)
    authed.post("/m/api/exchange", json={"text": "курс usdt/usd 0.999 источник binance"})
    qid = authed.post("/m/api/exchange", json={"text": "100 usdt в czk"}).json()["quote_id"]
    a.stop_file.write_text("stop")
    r = authed.post("/m/api/exchange", json={"text": f"обмен выполнен {qid}"}).json()
    assert not r["ok"] and r["stopped"] and "СТОП" in r["reply"]
    assert authed.c.get("/m/api/exchange/deals").json()["deals"] == []


def test_input_limits(authed, tmp_path):
    authed.app.state.exchange = _assistant(tmp_path)
    assert authed.post("/m/api/exchange", json={"text": ""}).status_code == 422
    assert authed.post("/m/api/exchange", json={"text": "x" * 301}).status_code == 422
    r = authed.post("/m/api/exchange", json={"text": "сделай что-нибудь"}).json()
    assert "Не понял" in r["reply"]
