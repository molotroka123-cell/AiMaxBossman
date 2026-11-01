import httpx


def test_mobile_task_passes_backend_failure_summary(authed, monkeypatch):
    summary = "Я приступаю\n\nДействие не подтверждено. Проверьте инструменты."
    original_handler = authed.fake.handler

    def backend(request):
        if request.url.path == "/api/tasks/7":
            return httpx.Response(200, json={
                "task": {"id": 7, "status": "failed", "agent_id": 1},
                "result": None, "error": "action_contract/no_verified_action",
                "failure_summary": summary, "runs": [],
            })
        return original_handler(request)

    monkeypatch.setattr(authed.app.state.bossman.http, "_transport", httpx.MockTransport(backend))
    response = authed.c.get("/m/api/tasks/7")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "action_contract/no_verified_action"
    assert body["result"] is None
    assert body["failure_summary"] == summary
