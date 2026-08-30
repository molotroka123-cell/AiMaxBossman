
from fastapi.testclient import TestClient
from file_commander_mini.api import build_app
def test_file_flow(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path/"data"))
    monkeypatch.setenv("FILE_COMMANDER_ROOTS",str(tmp_path))
    (tmp_path/"a.pdf").write_text("same");(tmp_path/"b.pdf").write_text("same")
    c=TestClient(build_app())
    d=c.post("/api/files/duplicates",json={"root":str(tmp_path)}).json()
    assert len(d["groups"])==1
    p=c.post("/api/files/organize-plan",json={"root":str(tmp_path)}).json()
    assert p["count"]==2
    preview=c.post("/api/files/apply",json={"operations":p["operations"],"approve":False}).json()
    assert preview["status"]=="PREVIEW_ONLY"
