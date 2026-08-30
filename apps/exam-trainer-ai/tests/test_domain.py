
from fastapi.testclient import TestClient
from exam_trainer_ai.api import build_app
def test_exam_flow(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    exam=c.post("/api/exams",json={"name":"Demo","language":"en","topics":[{"name":"math","weight":2},{"name":"logic","weight":1}]}).json()
    eid=exam["id"]
    qs=c.post(f"/api/exams/{eid}/questions",json={"questions":[{"topic":"math","prompt":"2+2","answer":"4"},{"topic":"logic","prompt":"A?","answer":"A"}]}).json()["questions"]
    d=c.post(f"/api/exams/{eid}/diagnostic",json={"answers":{qs[0]["id"]:"4",qs[1]["id"]:"B"}}).json()
    assert d["accuracy"]==0.5
    r=c.post(f"/api/exams/{eid}/readiness",json={"mastery":{"math":1,"logic":0}}).json()
    assert 60 < r["readiness_score"] < 70
