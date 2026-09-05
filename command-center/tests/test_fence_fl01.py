"""FL-01 / TZ-05 §2 — fencing-токен поверх аренды run'а (INV-2: единственность
сайд-эффекта). Приёмка: test_fence_rejects_zombie_writer,
test_heartbeat_conditional_on_fence, test_idempotent_external_effect.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import task_runs as runs_t, tool_calls as tool_calls_t, utcnow
from bcc.engine import FencedOut, TaskEngine
from bcc.tools import ToolResult, ToolSpec

from .helpers import make_stack


async def _expire_lease(db, run_id: int) -> None:
    async with db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
            worker_lease_until=utcnow() - timedelta(seconds=5)))
        await s.commit()


async def _run_row(db, run_id: int) -> dict:
    async with db.session() as s:
        return dict((await s.execute(sa.select(runs_t).where(runs_t.c.id == run_id))).first()._mapping)


async def _tool_rows(db, task_id: int) -> list[dict]:
    async with db.session() as s:
        rows = (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.task_id == task_id).order_by(tool_calls_t.c.id))).fetchall()
    return [dict(r._mapping) for r in rows]


def _second_engine(env) -> TaskEngine:
    b = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry, lease_seconds=1, heartbeat_seconds=1)
    b.services = env.svc
    return b


def _call(name="terminal_run", cid="c1", **args):
    return SimpleNamespace(id=cid, name=name, arguments=args)


async def _takeover(env, a: TaskEngine, run_id: int) -> TaskEngine:
    """A «замирает»: аренда истекает, B делает recover и забирает run."""
    await _expire_lease(env.svc.db, run_id)
    b = _second_engine(env)
    assert await b.recover() == 1
    assert await b.claim() == run_id
    return b


# 1. зомби-писатель отвергается: один receipt в БД, статус run — от B
async def test_fence_rejects_zombie_writer(env):
    stack = await make_stack(env.client)
    a = env.svc.engine
    run_id = await a.claim()
    fence_a = a.fence_of(run_id)
    assert fence_a == 1
    b = await _takeover(env, a, run_id)
    assert b.fence_of(run_id) == 3          # recover +1, claim +1
    assert (await _run_row(env.svc.db, run_id))["fence"] == 3

    spec = ToolSpec(name="terminal.run", description="", handler=None, permission="terminal.run",
                    default_effect="auto", idempotent=False)  # type: ignore[arg-type]
    task_id = stack["task"]["id"]
    # A пытается записать receipt — отказ до записи
    with pytest.raises(FencedOut):
        await a._record_tool_call(run_id, task_id, 0, _call(command="echo A"), spec,
                                  effect="auto", status="executed", preview="A")
    # A пытается закрыть run — отказ; статус остаётся leased (держатель — B)
    with pytest.raises(FencedOut):
        await a._finish(run_id, task_id, "completed", result="A")
    with pytest.raises(FencedOut):
        await a._save_checkpoint(run_id, [], 1, note="A")
    row = await _run_row(env.svc.db, run_id)
    assert row["status"] == "leased" and row["result"] is None and row["fence"] == 3
    assert await _tool_rows(env.svc.db, task_id) == []
    # B пишет нормально
    await b._record_tool_call(run_id, task_id, 0, _call(command="echo B"), spec,
                              effect="auto", status="executed", preview="B")
    rows = await _tool_rows(env.svc.db, task_id)
    assert len(rows) == 1 and rows[0]["result_preview"] == "B"
    await b._finish(run_id, task_id, "completed", result="B")
    assert (await _run_row(env.svc.db, run_id))["status"] == "completed"


# 2. heartbeat условен по fence: 0 строк → отмена задачи-владельца, run не трогается
async def test_heartbeat_conditional_on_fence(env):
    await make_stack(env.client)
    a = env.svc.engine
    run_id = await a.claim()
    assert await a._heartbeat_once(run_id) is True
    b = await _takeover(env, a, run_id)
    before = await _run_row(env.svc.db, run_id)
    assert await a._heartbeat_once(run_id) is False
    assert await b._heartbeat_once(run_id) is True

    async def frozen_owner():
        await asyncio.sleep(30)

    owner = asyncio.create_task(frozen_owner())
    a.heartbeat_seconds = 0.01
    await asyncio.wait_for(a._heartbeat(run_id, owner), timeout=5)
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert owner.cancelled() and run_id in a._fenced_out
    after = await _run_row(env.svc.db, run_id)
    assert after["status"] == before["status"] == "leased" and after["fence"] == 3


# 2b. execute() зомби после перехвата выходит без записи статуса
async def test_zombie_execute_exits_without_writing(env):
    from tests.conftest import FakeAdapter

    async def slow(_calls, _messages):
        await asyncio.sleep(0.5)

    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ответ A", on_chat=slow)
    await make_stack(env.client)
    a = env.svc.engine
    a.heartbeat_seconds = 0.02
    run_id = await a.claim()
    b = await _takeover(env, a, run_id)
    await asyncio.wait_for(a.execute(run_id), timeout=10)   # не бросает, не пишет
    row = await _run_row(env.svc.db, run_id)
    assert row["status"] == "leased" and row["result"] is None and row["fence"] == b.fence_of(run_id)
    async with env.svc.db.session() as s:
        from bcc.db import run_events as events_t
        kinds = [r[0] for r in (await s.execute(sa.select(events_t.c.kind).where(
            events_t.c.run_id == run_id))).fetchall()]
    assert "run.fenced_out" in kinds
    assert not any(k in kinds for k in ("run.completed", "task.completed"))


# 3. неидемпотентный внешний эффект не повторяется другой попыткой
async def test_idempotent_external_effect(env):
    stack = await make_stack(env.client, max_retries=3)
    task_id = stack["task"]["id"]
    calls = {"n": 0}

    async def handler(args, ctx):
        calls["n"] += 1
        return ToolResult(content=f"отправлено #{calls['n']}", one_line="ок")

    spec = ToolSpec(name="mail.send", description="", handler=handler, permission="",
                    default_effect="auto", idempotent=False)
    a = env.svc.engine
    run1 = await a.claim()
    task = {"id": task_id, "workspace_path": ""}
    agent = {"name": "аналитик", "permissions": {}}
    messages: list[dict] = []
    await a._run_tool_now(run1, task, agent, messages, _call("mail_send", "x1", to="a@b"), spec, 0)
    assert calls["n"] == 1
    # «рестарт между эффектом и checkpoint»: аренда истекла, попытка 2 берёт run заново
    b = await _takeover(env, a, run1)
    messages2: list[dict] = []
    await b._run_tool_now(run1, task, agent, messages2, _call("mail_send", "x2", to="a@b"), spec, 0)
    assert calls["n"] == 1, "duplicate_side_effect_count должен быть 0"
    # тот же шаг в ДРУГОМ run'е той же задачи (новая попытка через enqueue) — тоже не повторяется
    run2 = await b.enqueue(task_id, attempt=1)
    b._fences[run2] = (await _run_row(env.svc.db, run2))["fence"] or 0
    messages3: list[dict] = []
    await b._run_tool_now(run2, task, agent, messages3, _call("mail_send", "x3", to="a@b"), spec, 0)
    assert calls["n"] == 1
    rows = await _tool_rows(env.svc.db, task_id)
    assert [r["status"] for r in rows if r["run_id"] == run2] == ["replayed"]
    assert "уже исполнен" in messages3[-1]["content"]
    # другие аргументы — новый эффект (это не дубль)
    await b._run_tool_now(run2, task, agent, messages3, _call("mail_send", "x4", to="c@d"), spec, 0)
    assert calls["n"] == 2
    # идемпотентный инструмент повторяется свободно
    idem = ToolSpec(name="fs.read", description="", handler=handler, permission="",
                    default_effect="auto", idempotent=True)
    await b._run_tool_now(run2, task, agent, messages3, _call("fs_read", "x5", to="c@d"), idem, 0)
    assert calls["n"] == 3
