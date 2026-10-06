"""A non-finite amount must never enter the ledger.

Before the fix, one CSV row with amount "nan", "inf" or "1e309" was stored;
from then on GET /api/transactions, /api/reports/pnl and /api/reports/cashflow
answered 500 (NaN/Infinity cannot be serialised to JSON) until the database
was edited by hand.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from bossman_accountant.api import build_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    return TestClient(build_app(), raise_server_exceptions=False)


def test_csv_with_non_finite_amounts_is_refused_and_reports_stay_up(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    csv = ("date,amount,description\n"
           "2026-08-01,1000,sale\n"
           "2026-08-02,nan,bad\n"
           "2026-08-03,inf,bad\n"
           "2026-08-04,1e309,bad\n"
           "2026-08-05,-Infinity,bad\n")
    res = c.post("/api/transactions/import-csv", json={"text": csv}).json()
    assert res["added"] == 1
    assert res["rejected"] == 4
    for path in ("/api/transactions", "/api/reports/pnl", "/api/reports/cashflow"):
        assert c.get(path).status_code == 200, path
    assert [t["amount"] for t in c.get("/api/transactions").json()["transactions"]] == [1000.0]


def test_json_import_with_non_finite_amount_is_refused(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    rows = [{"date": "2026-08-01", "amount": 10, "description": "ok"},
            {"date": "2026-08-02", "amount": "nan", "description": "bad"}]
    res = c.post("/api/transactions/import", json={"rows": rows}).json()
    assert res["added"] == 1 and res["rejected"] == 1
    assert c.get("/api/reports/pnl").status_code == 200
