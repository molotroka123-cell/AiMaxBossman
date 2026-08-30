
from fastapi.testclient import TestClient
from exam_trainer_ai.api import build_app
def test_session_mock(tmp_path,monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA",str(tmp_path))
    c=TestClient(build_app())
    e=c.post("/api/exams",json={"name":"X","topics":[{"name":"m","weight":1}]}).json()
    q=c.post(f"/api/exams/{e['id']}/questions",json={"questions":[{"topic":"m","prompt":"1+1","answer":"2","explanation":"basic"}]}).json()["questions"][0]
    s=c.post(f"/api/exams/{e['id']}/sessions",json={"learner_id":"u"}).json()
    assert c.post(f"/api/sessions/{s['id']}/answer",json={"question_id":q["id"],"answer":"2"}).json()["correct"] is True
    assert c.post(f"/api/exams/{e['id']}/mock",json={"count":1}).json()["answer_key_hidden"] is True
