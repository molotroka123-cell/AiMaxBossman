
from fastapi.testclient import TestClient
from bossman_accountant.api import build_app
def test_csv_anomaly_compare(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    csv="date,amount,description\n2026-08-01,1000,sale\n2026-08-02,-5000,rent\n"
    assert c.post("/api/transactions/import-csv",json={"text":csv}).json()["added"]==2
    assert "anomalies" in c.get("/api/reports/anomalies").json()
    assert c.get("/").status_code==200
