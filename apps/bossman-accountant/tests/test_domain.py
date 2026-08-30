
import os
from fastapi.testclient import TestClient
from bossman_accountant.api import build_app
def test_accounting_flow(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    r=c.post("/api/transactions/import",json={"rows":[
      {"date":"2026-08-01","amount":1000,"description":"sale"},
      {"date":"2026-08-02","amount":-200,"description":"software hosting"}]})
    assert r.json()["added"]==2
    p=c.get("/api/reports/pnl").json()
    assert p["profit"]==800
    assert c.get("/api/reports/health-score").json()["score"]>=50
    w=c.post("/api/forecast/what-if",json={"monthly_revenue_change":100,"monthly_cost_change":50}).json()
    assert w["projected_profit"]==850
