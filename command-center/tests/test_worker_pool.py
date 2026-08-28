"""Worker Pool и Hard Cancel (core-1 ночного бэклога).

Раньше worker был последовательным (одна задача за раз) и Stop был мягким
(ждал конца шага). Теперь: до N параллельных run'ов; Stop рвёт активный вызов.
"""
import asyncio

import sqlalchemy as sa

from bcc.db import task_runs, tasks
from bcc.providers import ChatResult

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack


class SlowAdapter(FakeAdapter):
    """Адаптер с медленным «инференсом» и счётчиком одновременных вызовов."""

    concurrent = 0
    max_concurrent = 0

    def __init__(self, delay: float):
        super().__init__("медленный ответ")
        self.delay = delay

    async def chat(self, model, messages, **kw):
        SlowAdapter.concurrent += 1
        SlowAdapter.max_concurrent = max(SlowAdapter.max_concurrent, SlowAdapter.concurrent)
        try:
            await asyncio.sleep(self.delay)
        finally:
            SlowAdapter.concurrent -= 1
        return ChatResult(text="медленный ответ", tokens_in=5, tokens_out=2)


async def _task_status(client, task_id):
    return (await client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


async def test_two_slow_tasks_run_concurrently(env):
    SlowAdapter.concurrent = SlowAdapter.max_concurrent = 0
    env.svc.registry.adapter_factory = lambda m, p: SlowAdapter(0.4)
    env.svc.engine.workers = 2
    env.svc.engine.poll_interval = 0.02
    stack = await make_stack(env.client)
    second = (await env.client.post("/api/tasks", json={
        "title": "вторая", "prompt": "тоже долго", "agent_id": stack["agent"]["id"],
        "run_now": True})).json()["task"]

    loop = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        await wait_for(lambda: _both_done(env, stack["task"]["id"], second["id"]), timeout=6)
    finally:
        loop.cancel()
    assert SlowAdapter.max_concurrent == 2      # реально параллельно, не по очереди


def _both_done(env, a, b):
    async def check():
        sa_ = await _task_status(env.client, a)
        sb = await _task_status(env.client, b)
        return sa_ == "completed" and sb == "completed"
    return check()


async def test_hard_cancel_interrupts_inflight_inference(env):
    env.svc.registry.adapter_factory = lambda m, p: SlowAdapter(30.0)  # «вечный» inference
    env.svc.engine.workers = 1
    env.svc.engine.poll_interval = 0.02
    stack = await make_stack(env.client)

    loop = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        await wait_for(lambda: _running(env, stack["task"]["id"]), timeout=5)
        t0 = asyncio.get_running_loop().time()
        await env.client.post(f"/api/tasks/{stack['task']['id']}/stop")
        # ждём финализации именно RUN'а (статус задачи стал stopped мгновенно)
        await wait_for(lambda: _run_stopped(env, stack["task"]["id"]), timeout=3)
        elapsed = asyncio.get_running_loop().time() - t0
    finally:
        loop.cancel()
    assert elapsed < 2.5, f"hard cancel занял {elapsed:.1f}с — Stop не оборвал inference"
    async with env.svc.db.session() as s:
        run = (await s.execute(sa.select(task_runs))).fetchall()[-1]._mapping
    assert run["status"] == "stopped"


def _run_stopped(env, task_id):
    async def check():
        async with env.svc.db.session() as s:
            res = await s.execute(sa.select(task_runs.c.status).where(
                task_runs.c.task_id == task_id))
            row = res.first()
        return bool(row and row[0] == "stopped")
    return check()


def _running(env, task_id):
    async def check():
        async with env.svc.db.session() as s:
            res = await s.execute(sa.select(task_runs.c.status).where(
                task_runs.c.task_id == task_id))
            row = res.first()
        return bool(row and row[0] == "running")
    return check()


def _stopped(env, task_id):
    async def check():
        return (await _task_status(env.client, task_id)) == "stopped"
    return check()


async def test_pool_default_from_env(env, monkeypatch):
    from bcc.engine import TaskEngine
    monkeypatch.setenv("BCC_WORKERS", "5")
    engine = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry)
    assert engine.workers == 5
