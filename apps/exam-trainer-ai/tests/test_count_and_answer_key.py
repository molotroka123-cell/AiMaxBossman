"""count must be a sane positive number; the answer key stays hidden from learner-facing listings.

Before the fix: count=-1 returned «all but the last» question (qs[:-1]); count=10**9 built a list of a
billion entries in training_set; GET /questions (and the training set) returned every answer and
explanation, so the mock exam's answer_key_hidden meant nothing.
"""
import pytest
from fastapi.testclient import TestClient
from exam_trainer_ai.api import build_app


@pytest.fixture()
def exam(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    c = TestClient(build_app())
    e = c.post("/api/exams", json={"name": "X", "topics": [{"name": "m", "weight": 1}]}).json()
    qs = [{"topic": "m", "prompt": f"{i}+1", "answer": str(i + 1), "explanation": "sum"} for i in range(3)]
    c.post(f"/api/exams/{e['id']}/questions", json={"questions": qs})
    return c, e["id"]


@pytest.mark.parametrize("count", [-1, 0, 10**9])
@pytest.mark.parametrize("path", ["mock", "training"])
def test_bad_count_is_422(exam, path, count):
    c, eid = exam
    r = c.post(f"/api/exams/{eid}/{path}", json={"count": count})
    assert r.status_code == 422, r.text


def test_valid_counts_still_work(exam):
    c, eid = exam
    assert len(c.post(f"/api/exams/{eid}/mock", json={"count": 2}).json()["questions"]) == 2
    assert len(c.post(f"/api/exams/{eid}/training", json={"count": 5}).json()["questions"]) == 5


def test_question_listing_and_training_hide_the_answer_key(exam):
    c, eid = exam
    for body in (c.get(f"/api/exams/{eid}/questions").json(),
                 c.post(f"/api/exams/{eid}/training", json={"count": 3}).json()):
        assert body["answer_key_hidden"] is True
        assert body["questions"] and all("answer" not in q and "explanation" not in q for q in body["questions"])
    full = c.get(f"/api/exams/{eid}/questions", params={"include_answers": "true"}).json()
    assert full["answer_key_hidden"] is False
    assert sorted(q["answer"] for q in full["questions"]) == ["1", "2", "3"]


def test_answers_are_still_checked_server_side(exam):
    c, eid = exam
    q = c.get(f"/api/exams/{eid}/questions").json()["questions"][0]
    s = c.post(f"/api/exams/{eid}/sessions", json={"learner_id": "u"}).json()
    right = str(int(q["prompt"].split("+")[0]) + 1)
    assert c.post(f"/api/sessions/{s['id']}/answer", json={"question_id": q["id"], "answer": right}).json()["correct"] is True
