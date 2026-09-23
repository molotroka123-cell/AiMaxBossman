"""Backend side of the 1.2 terminal: reasoning is never synthesised, the
per-task filter follows runs/approvals, stream kinds stay out of the activity
feed, history replay is ordered and exact, and the engine's new emissions do
not change the run."""
from __future__ import annotations

from bcc.events import STREAM_ONLY, TRANSIENT, TaskStreamFilter
from bcc.streaming import message_reasoning

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for


def test_message_reasoning_is_only_what_the_provider_sent():
    assert message_reasoning({"content": "ответ"}) == ""
    assert message_reasoning({"reasoning_content": "  думаю  "}) == "думаю"
    assert message_reasoning({"reasoning": [{"type": "text", "text": "шаг"}]}) == "шаг"
    assert message_reasoning({"content": [{"type": "thinking", "thinking": "мысль"},
                                          {"type": "text", "text": "ответ"}]}) == "мысль"
    assert message_reasoning(None) == ""


def test_reasoning_is_never_persisted_but_other_stream_kinds_are():
    assert "run.reasoning_delta" in TRANSIENT
    assert "run.tool_use" in STREAM_ONLY and "run.tool_use" not in TRANSIENT


def test_task_stream_filter_learns_runs_and_approvals():
    f = TaskStreamFilter(task_id=5)
    assert f.accept({"kind": "task.queued", "task_id": 5, "run_id": 9})
    assert f.accept({"kind": "run.log", "run_id": 9})
    assert not f.accept({"kind": "run.log", "run_id": 10})
    assert f.accept({"kind": "approval.created", "id": 3, "task_id": 5, "run_id": 9})
    assert f.accept({"kind": "approval.decided", "id": 3})
    assert not f.accept({"kind": "approval.decided", "id": 4})
    assert not f.accept({"kind": "task.queued", "task_id": 6, "run_id": 9})
    assert not f.accept({"kind": "system.metrics"})


async def test_engine_emits_step_events_and_history_replays_them(tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: FakeAdapter("готово"))
    try:
        async with client_for(app, svc) as c:
            prov = (await c.post("/api/providers", json={"name": "p", "kind": "openai_compat",
                                                         "base_url": "http://127.0.0.1:1"})).json()
            model = (await c.post("/api/models", json={"provider_id": prov["id"], "name": "m"})).json()
            agent = (await c.post("/api/agents", json={"name": "a", "model_id": model["id"]})).json()
            task = (await c.post("/api/tasks", json={"prompt": "привет", "agent_id": agent["id"],
                                                     "client_request_id": "cli-test-000001"})).json()
            tid = task["task"]["id"]

            async def done():
                t = (await c.get(f"/api/tasks/{tid}")).json()["task"]
                return t["status"] in ("completed", "failed")
            await wait_for(done, timeout=20)
            assert (await c.get(f"/api/tasks/{tid}")).json()["task"]["status"] == "completed"
            page = (await c.get(f"/api/tasks/{tid}/events")).json()
            kinds = [e["kind"] for e in page["events"]]
            assert "run.assistant_message" in kinds and "run.usage" in kinds
            assert "run.reasoning_delta" not in kinds
            seqs = [e["seq"] for e in page["events"]]
            assert seqs == sorted(seqs) and page["cursor"] == seqs[-1]
            empty = (await c.get(f"/api/tasks/{tid}/events", params={"after": page["cursor"]})).json()
            assert empty["events"] == []
            activity = (await c.get("/api/activity", params={"limit": 200})).json()
            assert not [e for e in activity if e["kind"] in STREAM_ONLY]
            again = (await c.post("/api/tasks", json={"prompt": "привет", "agent_id": agent["id"],
                                                      "client_request_id": "cli-test-000001"})).json()
            assert again["replayed"] is True and again["task"]["id"] == tid
    finally:
        await svc.stop()
