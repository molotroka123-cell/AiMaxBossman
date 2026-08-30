
from fastapi.testclient import TestClient
from pc_autopilot_mini.api import build_app
def test_macro_flow(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path/"data"))
    src=tmp_path/"a.txt";src.write_text("x")
    dst=tmp_path/"b.txt"
    c=TestClient(build_app())
    m=c.post("/api/macros",json={"name":"copy","steps":[{"action":"file.copy","src":str(src),"dst":str(dst)},{"action":"verify.exists","path":str(dst)}]}).json()
    prev=c.post(f"/api/macros/{m['id']}/run",json={"approve":False}).json()
    assert prev["status"]=="PREVIEW_ONLY"
    done=c.post(f"/api/macros/{m['id']}/run",json={"approve":True}).json()
    assert done["status"]=="COMPLETED" and dst.exists()
    ui=c.post("/api/macros",json={"name":"ui","steps":[{"action":"ui.click","selector":"Import"}]}).json()
    paused=c.post(f"/api/macros/{ui['id']}/run",json={"approve":True}).json()
    assert paused["status"]=="PAUSED_NEEDS_BOSSMAN"
