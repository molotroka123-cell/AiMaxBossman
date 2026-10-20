"""CSV import must not silently lose rows, and reports must show cents, not float noise.

Before the fix: «1,234.56» (and «1.234,56») became «1.234.56» -> ValueError -> row skipped without
being counted; by_category printed 0.30000000000000004; a JSON row dated «2026-08-31T23:00:00»
was stored verbatim and fell out of every report ending on 2026-08-31; a Czech-bank date
«31.08.2026 14:30» was stored as «31.08.2026» and was never inside any ISO period.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bossman_accountant.api import build_app


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    return TestClient(build_app(), raise_server_exceptions=False)


def _amounts(c):
    return sorted(t["amount"] for t in c.get("/api/transactions").json()["transactions"])


@pytest.mark.parametrize("raw,expected", [
    ('"1,234.56"', 1234.56),
    ('"1.234,56"', 1234.56),
    ('"1 234,56"', 1234.56),
    ('"-2,500.00"', -2500.0),
    ('"12,5"', 12.5),
    ("99.90", 99.9),
])
def test_thousands_separators_are_parsed_not_dropped(c, raw, expected):
    res = c.post("/api/transactions/import-csv", json={"text": f"date,amount,description\n2026-08-01,{raw},x\n"}).json()
    assert res["added"] == 1, res
    assert _amounts(c) == [expected]


def test_unparseable_rows_are_counted_as_rejected(c):
    csv = ("date,amount,description\n"
           "2026-08-01,100,ok\n"
           "2026-08-02,abc,bad amount\n"
           "2026-08-03,,no amount\n"
           ",50,no date\n"
           "not-a-date,50,bad date\n")
    res = c.post("/api/transactions/import-csv", json={"text": csv}).json()
    assert res["added"] == 1 and res["rejected"] == 4, res


def test_json_import_accepts_separated_amount_strings(c):
    rows = [{"date": "2026-08-01", "amount": "1,234.56", "description": "invoice paid"},
            {"date": "2026-08-02", "amount": "-1 000,50", "description": "rent"}]
    res = c.post("/api/transactions/import", json={"rows": rows})
    assert res.status_code == 200 and res.json()["added"] == 2
    assert c.get("/api/reports/pnl").json()["by_category"] == {"rent": -1000.5, "sales": 1234.56}


def test_category_totals_are_rounded_to_cents(c):
    rows = [{"date": "2026-08-01", "amount": 0.1, "description": "a", "category": "x"},
            {"date": "2026-08-02", "amount": 0.2, "description": "b", "category": "x"}]
    c.post("/api/transactions/import", json={"rows": rows})
    assert c.get("/api/reports/pnl").json()["by_category"] == {"x": 0.3}


@pytest.mark.parametrize("dt", ["2026-08-31T23:00:00", "2026-08-31 23:00", "2026-08-31T23:00:00+02:00",
                                "31.08.2026", "31.08.2026 14:30", "31.8.2026"])
def test_dates_with_time_or_czech_format_stay_inside_their_day(c, dt):
    csv = f"date,amount,description\n{dt},100,sale\n"
    assert c.post("/api/transactions/import-csv", json={"text": csv}).json()["added"] == 1
    c.post("/api/transactions/import", json={"rows": [{"date": dt, "amount": 5, "description": "json"}]})
    assert {t["date"] for t in c.get("/api/transactions").json()["transactions"]} == {"2026-08-31"}
    assert c.get("/api/reports/pnl", params={"start": "2026-08-01", "end": "2026-08-31"}).json()["income"] == 105.0
