
from fastapi.testclient import TestClient
from pc_autopilot_mini.api import build_app
def test_teach_trigger(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path/"data"))
    p=tmp_path/"ready.txt";p.write_text("ok")
    c=TestClient(build_app())
    m=c.post("/api/teach",json={"name":"m","events":[{"type":"wait","seconds":0}]}).json()
    c.post("/api/triggers",json={"macro_id":m["id"],"kind":"file_exists","config":{"path":str(p)}})
    assert len(c.get("/api/triggers/evaluate").json()["hits"])==1
