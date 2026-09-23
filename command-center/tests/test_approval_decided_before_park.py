"""An owner's decision that lands before the engine finishes parking is not lost.

Parking a tool call for approval is three separate commits:

    1. approvals row created (already visible in /api/approvals and pushed
       to the UI via `approval.created`),
    2. tool_calls row written with status=pending_approval,
    3. task -> waiting_approval (`_park_for_approval`).

`on_approval_decided` resumes a task by looking up the pending tool_calls
row. A decision committed between 1 and 2 found no row and was dropped; a
decision between 2 and 3 set the task `queued`, and step 3 then overwrote it
back to `waiting_approval`. Either way the task hung with an approved
approval until the periodic recover() sweep (every 60 s) noticed it. That is
what made test_action_contract's CODE_ACTION case flip to
`waiting_approval` on slow CI runners: its driver approves anything pending
as soon as it sees it, i.e. sometimes inside that window.

These tests force the decision into each window deterministically (no
timing), and require the task to finish without the recover sweep.
"""
from __future__ import annotations

import asyncio

import pytest

from .test_action_contract import _allow_root
from .test_v21_tool_loop import FINISHED, ToolAdapter, _stack_with_tools
from .conftest import wait_for


async def _run_until_finished(env, task_id, *, timeout: float = 10.0) -> str | None:
    """Worker + approval watcher, NO approving driver and NO recover sweep:
    only the decision injected by the test may resume the task."""
    env.svc.engine.poll_interval = 0.02
    env.svc.engine.recover_every = 3600.0      # the 60 s safety net must not mask the bug
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        async def done():
            status = (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]
            return status if status in FINISHED else None
        return await wait_for(done, timeout=timeout)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def _stack(env, tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    create_cmd = "python -c \"open('hello.txt','w').write('hi')\""
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": create_cmd, "mode": "project_host", "cwd": str(work)}),
        ("text", "Готово, файл создан."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Создай файл hello.txt через терминал", max_steps=6)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})
    return stack, work


def _track_watcher(engine, monkeypatch) -> dict:
    """Record every completed on_approval_decided(approval_id) so the test can
    wait for the watcher explicitly (an Event, not a sleep)."""
    handled: dict = {}
    original = engine.on_approval_decided

    async def on_decided(approval_id):
        try:
            return await original(approval_id)
        finally:
            handled.setdefault(int(approval_id), asyncio.Event()).set()
    monkeypatch.setattr(engine, "on_approval_decided", on_decided)
    return handled


async def _decide_once(env, handled: dict, approval_id, decided: list) -> None:
    if approval_id is None or approval_id in decided:
        return
    decided.append(approval_id)
    event = handled.setdefault(int(approval_id), asyncio.Event())
    # The real API path: commit, then `approval.decided` on the bus.
    await env.svc.approvals.decide(int(approval_id), True, by="owner")
    # Block the parking coroutine until the watcher has FULLY handled the
    # decision, i.e. force the worst-case interleaving: the watcher runs
    # strictly before the engine performs its next parking step.
    await asyncio.wait_for(event.wait(), timeout=10.0)


@pytest.mark.parametrize("window", ["before_tool_call_row", "before_task_parked"])
async def test_decision_inside_the_parking_window_resumes_the_task(env, tmp_path, monkeypatch, window):
    stack, work = await _stack(env, tmp_path)
    engine = env.svc.engine
    handled = _track_watcher(engine, monkeypatch)
    decided: list = []

    if window == "before_tool_call_row":
        original = engine._record_tool_call

        async def record(run_id, task_id, step, call, spec, *, status, approval_id=None, **kw):
            if status == "pending_approval":
                await _decide_once(env, handled, approval_id, decided)
            return await original(run_id, task_id, step, call, spec, status=status,
                                  approval_id=approval_id, **kw)
        monkeypatch.setattr(engine, "_record_tool_call", record)
    else:
        original = engine._park_for_approval

        async def park(run_id, task_id, messages, step, pending, usage):
            await _decide_once(env, handled, pending.get("approval_id"), decided)
            return await original(run_id, task_id, messages, step, pending, usage)
        monkeypatch.setattr(engine, "_park_for_approval", park)

    status = await _run_until_finished(env, stack["task"]["id"])
    assert decided, "the injected decision never ran — the test did not exercise the window"
    assert status == "completed", f"approved task hung in the parking window: {status!r}"
    assert (work / "hello.txt").read_text(encoding="utf-8") == "hi"


async def test_decision_after_parking_still_resumes_exactly_once(env, tmp_path):
    """Control: the ordinary path (decide after the task is parked) is unchanged
    and the command runs once, not twice, despite two resume triggers
    (post-park recheck is a no-op here; the watcher does the resume)."""
    stack, work = await _stack(env, tmp_path)
    env.svc.engine.poll_interval = 0.02
    env.svc.engine.recover_every = 3600.0
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        async def parked():
            t = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
            return t["status"] == "waiting_approval" or None
        assert await wait_for(parked, timeout=10.0)
        pending = (await env.client.get("/api/approvals?status=pending")).json()
        assert len(pending) == 1
        await env.client.post(f"/api/approvals/{pending[0]['id']}", json={"approve": True, "by": "test"})

        async def done():
            t = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
            return t["status"] if t["status"] in FINISHED else None
        assert await wait_for(done, timeout=10.0) == "completed"
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
    import sqlalchemy as sa
    from bcc import db as dbm
    async with env.svc.db.session() as s:
        executed = (await s.execute(sa.select(dbm.tool_calls).where(
            dbm.tool_calls.c.task_id == stack["task"]["id"],
            dbm.tool_calls.c.status == "executed"))).fetchall()
    assert len(executed) == 1
