
from fastapi.testclient import TestClient
from travel_architect.api import build_app
def test_travel_flow(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    trip=c.post("/api/trips",json={"origin":["PRG","VIE"],"destination":"TFS","start":"2026-10-01","end":"2026-10-08","travelers":2}).json()
    tid=trip["id"]
    a=c.post(f"/api/trips/{tid}/offers",json={"kind":"package","provider":"A","base_price":1200,"fees":{"transfer":100}}).json()
    b=c.post(f"/api/trips/{tid}/offers",json={"kind":"diy","provider":"B","base_price":1000,"fees":{"bags":50}}).json()
    assert a["true_price"]==1300 and b["true_price"]==1050
    best=c.get(f"/api/trips/{tid}/compare").json()["best"]
    assert best["provider"]=="B"
    arb=c.get(f"/api/trips/{tid}/package-arbitrage").json()
    assert arb["diy_saves_vs_package"]==250
