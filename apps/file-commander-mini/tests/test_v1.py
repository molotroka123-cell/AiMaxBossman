
from fastapi.testclient import TestClient
from file_commander_mini.api import build_app
def test_rules_summary(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path/"data"))
    monkeypatch.setenv("FILE_COMMANDER_ROOTS",str(tmp_path))
    (tmp_path/"x.pdf").write_text("x")
    c=TestClient(build_app())
    c.post("/api/rules",json={"name":"pdf","match_ext":[".pdf"],"target_dir":"PDF"})
    p=c.post("/api/files/rule-plan",json={"root":str(tmp_path)}).json()
    assert p["count"]==1
    s=c.post("/api/files/cleanup-summary",json={"root":str(tmp_path)}).json()
    assert s["count"]>=1
