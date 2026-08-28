"""Feature 03 Governor + 08 Reviewer Gate — на живом движке."""
import asyncio

import sqlalchemy as sa

from bcc.db import interventions as interv_t, tasks as tasks_t
from bcc.features.governor import _sig
from bcc.providers import ProviderError

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack


def test_error_signature_normalizes():
    assert _sig("timeout after 30 s at /a/b") == _sig("timeout after 5 s at /c/d")


async def test_governor_stops_error_loop(env):
    # адаптер всегда падает одной ошибкой; max_retries большой — остановить должен Governor
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(fail_times=99, error="boom")
    env.svc.engine.workers = 1
    env.svc.engine.poll_interval = 0.02
    env.svc.engine.retry_base_delay = 0.01
    # порог backstop 5; задача с большим max_retries — Governor остановит раньше движка
    await env.client.patch("/api/governor/rules", json={"repeated_error_limit": 5})
    stack = await make_stack(env.client, max_retries=20)
    loop = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        async def stopped():
            t = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
            return t["status"] == "stopped"
        await wait_for(stopped, timeout=12)
    finally:
        loop.cancel()
    interventions = (await env.client.get("/api/governor/interventions")).json()
    assert any(i["action"] == "stopped" for i in interventions)
    # не докрутил до 20 попыток — остановлен на пороге
    async with env.svc.db.session() as s:
        from bcc.db import task_runs
        runs = (await s.execute(sa.select(sa.func.count()).select_from(task_runs).where(
            task_runs.c.task_id == stack["task"]["id"]))).scalar_one()
    assert runs < 20


async def test_governor_no_progress_pause():
    from bcc.features.governor import _on_step
    from tests.conftest import make_settings, start_app, client_for
    import tempfile, pathlib
    settings = make_settings(pathlib.Path(tempfile.mkdtemp()))
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc):
        paused = {"v": False}

        async def fake_pause(task_id):
            paused["v"] = True
        svc.engine.pause = fake_pause
        hook = await _on_step(svc)
        # 6 одинаковых ответов → no-progress
        same = [{"role": "assistant", "content": "одно и то же"} for _ in range(6)]
        await hook({"id": 1}, 1, {"messages": same, "step": 6})
        assert paused["v"]
    await svc.stop()


async def test_reviewer_gate_fail_then_pass(env):
    """Кодер: 1-й ответ без «тест», 2-й с «тест» → FAIL→фидбек→PASS→completed."""
    calls = {"n": 0}

    class Coder(FakeAdapter):
        def __init__(self):
            super().__init__("")
        async def chat(self, model, messages, **kw):
            calls["n"] += 1
            text = "код без проверок" if calls["n"] == 1 else "код с тестами внутри"
            from bcc.providers import ChatResult
            return ChatResult(text=text, tokens_in=5, tokens_out=3)

    env.svc.registry.adapter_factory = lambda m, p: Coder()
    stack = await make_stack(env.client, max_steps=4)
    await env.client.post("/api/review/enable",
                          json={"task_id": stack["task"]["id"], "criteria": "тест",
                                "max_review_retries": 2})
    await env.client.post(f"/api/tasks/{stack['task']['id']}/retry")
    for _ in range(10):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    t = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert t["status"] == "completed"
    status = (await env.client.get(f"/api/review/status?task_id={stack['task']['id']}")).json()
    assert any(not h["passed"] for h in status["history"])   # был FAIL
    assert any(h["passed"] for h in status["history"])       # и потом PASS


async def test_reviewer_gate_escalates_after_limit(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("плохой код навсегда")
    stack = await make_stack(env.client, max_steps=6)
    await env.client.post("/api/review/enable",
                          json={"task_id": stack["task"]["id"], "criteria": "НЕТ_ТАКОГО",
                                "max_review_retries": 2})
    await env.client.post(f"/api/tasks/{stack['task']['id']}/retry")
    for _ in range(12):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    t = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert t["status"] == "waiting_approval"
    approvals = (await env.client.get("/api/approvals?status=pending")).json()
    assert any(a["kind"] == "review_escalation" for a in approvals)
