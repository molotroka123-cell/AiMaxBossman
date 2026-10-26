from fastapi.testclient import TestClient

from exam_trainer_ai.api import build_app


def test_local_exam_to_practice_flow_and_answer_key_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    client = TestClient(build_app())
    page = client.get("/")
    assert page.status_code == 200
    for control in ('id="create-exam"', 'id="add-question"', 'id="start-practice"'):
        assert control in page.text
    assert "не гарантия результата экзамена" in page.text
    assert "не загружает утечки" in page.text

    exam = client.post("/api/exams", json={
        "name": "Local reading practice", "language": "en",
        "topics": [{"name": "Reading", "weight": 1}],
    }).json()
    question = client.post(f"/api/exams/{exam['id']}/questions", json={
        "questions": [{"topic": "Reading", "prompt": "Choose a greeting", "answer": "Hello",
                       "choices": ["Hello", "Goodbye"], "explanation": "Hello is a greeting.",
                       "source_type": "user_supplied"}],
    }).json()["questions"][0]
    learner_view = client.get(f"/api/exams/{exam['id']}/questions").json()
    assert learner_view["answer_key_hidden"] is True
    assert "answer" not in learner_view["questions"][0]
    assert learner_view["questions"][0]["choices"] == ["Hello", "Goodbye"]
    session = client.post(f"/api/exams/{exam['id']}/sessions", json={"learner_id": "synthetic-learner"}).json()
    answer = client.post(f"/api/sessions/{session['id']}/answer", json={
        "question_id": question["id"], "answer": "Hello",
    }).json()
    assert answer["correct"] is True
    report = client.get(f"/api/sessions/{session['id']}/report").json()
    assert report["diagnostic"]["answered"] == 1
    assert "not a guarantee" in report["readiness"]["basis"]
