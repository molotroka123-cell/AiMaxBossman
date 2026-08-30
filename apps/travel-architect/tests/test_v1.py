
from fastapi.testclient import TestClient
from travel_architect.api import build_app
def test_smart_compare(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    t=c.post("/api/trips",json={"origin":["PRG"],"destination":"PMI","start":"2026-09-01","end":"2026-09-05","budget":1200}).json()
    o=c.post(f"/api/trips/{t['id']}/offers",json={"kind":"package","provider":"A","base_price":900,"quality_score":4,"metadata":{"stops":0,"travel_hours":3}}).json()
    assert c.get(f"/api/trips/{t['id']}/smart-compare").json()["recommended"]["provider"]=="A"
    assert c.get(f"/api/trips/{t['id']}/offers/{o['id']}/constraint-check").json()["pass"] is True
