
from fastapi.testclient import TestClient
from file_commander_mini.api import build_app
def test_health(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    h=c.get("/health")
    assert h.status_code==200 and h.json()["status"]=="healthy"
    caps=c.get("/capabilities").json()
    assert caps["standalone"] is True and caps["imports_bossman"] is False
