"""Fable hardening — Crash Matrix cell "after external effect, before journal".

Execution Truth Constitution §7/§9: an irreversible action must not be duplicated
after crash/retry/restart, and an ambiguous post-crash state must stay explicit —
never silently replayed.

Before this hardening the engine wrote the tool_calls receipt only AFTER the
handler returned. A crash in that window left no trace of the dispatch, so the
next attempt's replay guard (`_prior_effect`) found nothing and ran the
non-idempotent handler again: duplicate_side_effect_count = 1.

Now a non-idempotent dispatch is journaled write-ahead (`started`). A takeover
marks orphaned dispatches `interrupted`; a later request for the same action
is parked behind an `effect_reconciliation` approval instead of being executed
or silently skipped. The owner decides: approve = the effect did not happen,
run it once; reject = do not re-run. Both are journaled.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import approvals as approvals_t, task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow
from bcc.engine import TaskEngine
from bcc.tools import ToolResult, ToolSpec

from .conftest import FakeAdapter
from .helpers import make_stack


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so no engine handler can swallow it."""


def _call(name="mail_send", cid="c1", **args):
    return SimpleNamespace(id=cid, name=name, arguments=args, raw_arguments="")


async def _rows(db, task_id):
    async with db.session() as s:
        rows = (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.task_id == task_id).order_by(tool_calls_t.c.id))).fetchall()
    return [dict(r._mapping) for r in rows]


async def _takeover(env, run_id):
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
            worker_lease_until=utcnow() - timedelta(seconds=5)))
        await s.commit()
    b = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry, lease_seconds=1, heartbeat_seconds=1)
    b.services = env.svc
    assert await b.recover() == 1
    assert await b.claim() == run_id
    return b


def _mail_spec(counter):
    async def handler(args, ctx):
        counter["n"] += 1
        return ToolResult(content=f"sent #{counter['n']}", one_line="ok")
    return ToolSpec(name="mail.send", description="", handler=handler, permission="",
                    default_effect="auto", idempotent=False)


async def _crash_after_effect(env, counter):
    """Attempt 1 dispatches the effect and dies before journaling its outcome."""
    stack = await make_stack(env.client, max_retries=3)
    # Агенту ВЫДАН инструмент, которым его здесь водят напрямую через
    # tool_specs=[spec]. В проде tool_specs и allowed_tools_for(task, agent)
    # совпадают по построению; фикстура моделировала run без единого
    # выданного инструмента, и проверка «инструмент всё ещё выдан» в момент
    # эффекта честно отвергала вызов как снятый. Грант — не ослабление:
    # утверждения об exactly-once и о reconciliation не изменились.
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"tools": ["mail.send"]})
    task_id = stack["task"]["id"]
    spec = _mail_spec(counter)
    a = env.svc.engine
    run1 = await a.claim()
    # Real rows: the approval digest (F-013) binds to the agent/task identity the
    # engine loads on resume, so a fake dict would be rejected as a mismatch.
    from bcc.db import agents as agents_t, fetch_one
    async with env.svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
        agent = await fetch_one(s, agents_t, stack["agent"]["id"])
    original = a._record_tool_call

    async def crashing_record(run_id, tid, step, call, sp, **kw):
        if kw.get("status") in ("executed", "error"):
            raise SimulatedCrash("process died after the effect, before the receipt")
        return await original(run_id, tid, step, call, sp, **kw)

    a._record_tool_call = crashing_record
    with pytest.raises(SimulatedCrash):
        await a._run_tool_now(run1, task, agent, [], _call(cid="x1", to="a@b"), spec, 0)
    assert counter["n"] == 1
    a._record_tool_call = original
    a._fences.pop(run1, None)
    a._held_since.pop(run1, None)
    return stack, task, agent, spec, run1


async def test_effect_dispatched_before_crash_is_never_silently_replayed(env):
    counter = {"n": 0}
    stack, task, agent, spec, run1 = await _crash_after_effect(env, counter)
    task_id = task["id"]
    rows = await _rows(env.svc.db, task_id)
    assert [r["status"] for r in rows] == ["started"], "dispatch must be journaled write-ahead"

    b = await _takeover(env, run1)
    messages: list[dict] = []
    waiting = await b._execute_tool_calls(run1, task, agent, messages, [_call(cid="x2", to="a@b")], 0,
                                          policy_rules=[], tool_specs=[spec], usage={})
    assert counter["n"] == 1, "duplicate_side_effect_count must be 0"
    assert waiting is True, "ambiguous prior effect must be parked for the owner, not executed"
    async with env.svc.db.session() as s:
        appr = [dict(r._mapping) for r in (await s.execute(sa.select(approvals_t))).fetchall()]
        status = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).scalar()
    assert status == "waiting_approval"
    assert [a["kind"] for a in appr] == ["effect_reconciliation"]
    assert "mail.send" in appr[0]["preview"] and "may" in appr[0]["preview"].lower()
    rows = await _rows(env.svc.db, task_id)
    by_call = {r["call_id"]: r["status"] for r in rows}
    assert by_call["x1"] == "interrupted"
    assert by_call["x2"] == "pending_approval"


@pytest.mark.parametrize("approve", [True, False])
async def test_owner_reconciles_ambiguous_effect_exactly_once(env, approve):
    counter = {"n": 0}
    stack, task, agent, spec, run1 = await _crash_after_effect(env, counter)
    task_id = task["id"]
    b = await _takeover(env, run1)
    from bcc.tools import REGISTRY
    REGISTRY.register(spec)
    try:
        messages: list[dict] = [{"role": "user", "content": "send the mail"}]
        assert await b._execute_tool_calls(run1, task, agent, messages, [_call(cid="x2", to="a@b")], 0,
                                           policy_rules=[], tool_specs=[spec], usage={}) is True
        async with env.svc.db.session() as s:
            aid = (await s.execute(sa.select(approvals_t.c.id))).scalar()
        await env.svc.approvals.decide(aid, approve, by="owner")
        await b.on_approval_decided(aid)
        env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
        b._fences.pop(run1, None); b._held_since.pop(run1, None)
        assert await b.claim() == run1
        await b.execute(run1)
    finally:
        REGISTRY.unregister(spec.name)
    rows = await _rows(env.svc.db, task_id)
    by_call = {r["call_id"]: r for r in rows}
    assert by_call["x1"]["status"] == "reconciled", {k: (v["status"], v["approved_by"], v["error"]) for k, v in by_call.items()}
    if approve:
        assert counter["n"] == 2, "owner said the effect did not happen: run it exactly once"
        assert by_call["x2"]["status"] == "executed"
    else:
        assert counter["n"] == 1, "owner said do not re-run: no effect"
        assert by_call["x2"]["status"] == "rejected"
    events = await env.svc.bus.recent(300)
    assert any(e.get("kind") == "tool.ambiguous_effect" for e in events)


async def test_takeover_marks_orphaned_dispatch_even_without_a_retry_call(env):
    """A restart that never re-requests the tool must still not leave a live
    `started` row: finalize sees an explicit `interrupted` outcome."""
    counter = {"n": 0}
    stack, task, agent, spec, run1 = await _crash_after_effect(env, counter)
    b = await _takeover(env, run1)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово, письмо ушло")
    await b.execute(run1)
    rows = await _rows(env.svc.db, task["id"])
    assert [r["status"] for r in rows] == ["interrupted"]
    assert counter["n"] == 1
    async with env.svc.db.session() as s:
        status = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task["id"]))).scalar()
    assert status != "completed", "an unobserved irreversible effect is not a completed task"


# ---------------------------------------------------------------- hostile retest:
# the crash lands INSIDE the approval resume path — the owner approved the action
# once; the resumed attempt dispatched it and died before the receipt. An
# approval of the action is not an approval of its duplicate.

@pytest.mark.parametrize("approve_rerun", [True, False])
async def test_approved_call_crashed_after_effect_needs_owner_reconciliation(env, approve_rerun):
    from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools
    calls: list = []
    _install("terminal.run", calls=calls, permission="terminal.run", default_effect="ask", idempotent=False)
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "git push"}), ("text", "ок")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    async with env.svc.db.session() as s:
        aid = (await s.execute(sa.select(approvals_t.c.id))).scalar()
    await env.svc.approvals.decide(aid, True, by="owner")
    await env.svc.engine.on_approval_decided(aid)

    # resume on engine A: the approved dispatch happens, the receipt never lands
    a = env.svc.engine
    original = a._record_tool_call

    async def crashing_record(run_id, tid, step, call, sp, **kw):
        if kw.get("status") in ("executed", "error"):
            raise SimulatedCrash("died after the approved effect, before the receipt")
        return await original(run_id, tid, step, call, sp, **kw)

    a._record_tool_call = crashing_record
    run_id = await a.claim()
    with pytest.raises(SimulatedCrash):
        await a._run(run_id)
    a._record_tool_call = original
    a._fences.pop(run_id, None); a._held_since.pop(run_id, None)
    assert calls == [{"command": "git push"}]

    # takeover: the approved-and-interrupted call is NOT re-run on the old approval
    b = await _takeover(env, run_id)
    await b.execute(run_id)
    assert calls == [{"command": "git push"}], "duplicate_side_effect_count must be 0"
    async with env.svc.db.session() as s:
        appr = [dict(r._mapping) for r in (await s.execute(sa.select(approvals_t).order_by(approvals_t.c.id))).fetchall()]
        status = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).scalar()
    assert status == "waiting_approval"
    assert [x["kind"] for x in appr] == ["tool", "effect_reconciliation"]

    await env.svc.approvals.decide(appr[1]["id"], approve_rerun, by="owner")
    await b.on_approval_decided(appr[1]["id"])
    b._fences.pop(run_id, None); b._held_since.pop(run_id, None)
    assert await b.claim() == run_id
    await b.execute(run_id)
    rows = await _rows(env.svc.db, task_id)
    async with env.svc.db.session() as s:
        status = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).scalar()
    if approve_rerun:
        assert calls == [{"command": "git push"}] * 2
        assert rows[-1]["status"] == "executed" and status == "completed"
    else:
        assert calls == [{"command": "git push"}]
        assert rows[-1]["status"] == "rejected"
        assert status != "completed", "an unobserved effect the owner would not re-run is not a completion"


async def test_ask_tool_with_interrupted_prior_asks_reconciliation_not_plain_approval(env):
    """An ASK-policy action whose previous dispatch was interrupted must get ONE
    owner decision phrased as reconciliation — a plain "may it run" approval
    would hide that the effect may already exist."""
    from .test_v21_tool_loop import ToolAdapter, _install, _run_task, _stack_with_tools
    calls: list = []
    spec = _install("terminal.run", calls=calls, permission="terminal.run", default_effect="ask", idempotent=False)
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "git push"}), ("text", "ок")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    task_id = stack["task"]["id"]
    # forge the crash trace of an earlier attempt: an orphaned write-ahead row in another run
    from bcc.tools import args_hash
    async with env.svc.db.session() as s:
        old_run = (await s.execute(sa.insert(runs_t).values(task_id=task_id, status="failed", attempt=0))).inserted_primary_key[0]
        await s.execute(sa.insert(tool_calls_t).values(
            run_id=old_run, task_id=task_id, step=0, call_id="old", tool="terminal.run", source="terminal",
            args={"command": "git push"}, args_hash=args_hash("terminal.run", {"command": "git push"}),
            effect="ask", status="started", created_at=utcnow() - timedelta(minutes=5)))
        await s.commit()
    assert await _run_task(env, task_id) == "waiting_approval"
    assert calls == []
    async with env.svc.db.session() as s:
        appr = [dict(r._mapping) for r in (await s.execute(sa.select(approvals_t))).fetchall()]
    assert [a["kind"] for a in appr] == ["effect_reconciliation"]
    rows = await _rows(env.svc.db, task_id)
    assert {r["call_id"]: r["status"] for r in rows} == {"old": "interrupted", "call_1": "pending_approval"}


# ---------------------------------------------------------------- chaos cell:
# the journal write after the effect fails with a REAL database error (SQLite
# "database is locked" / disk pressure) instead of a process death. The engine's
# worker loop swallows the exception, the lease later expires, and recovery
# retries the attempt — which must not repeat the effect.

async def test_sqlite_lock_after_effect_is_recovered_without_duplicate(env):
    from sqlalchemy.exc import OperationalError
    from .test_v21_tool_loop import ToolAdapter, _install, _stack_with_tools
    calls: list = []
    spec = _install("mail.send", calls=calls, permission="", default_effect="auto", idempotent=False)
    adapter = ToolAdapter([("tool", "mail_send", {"text": "hello"}), ("text", "sent")])
    stack = await _stack_with_tools(env, ["mail.send"], adapter=adapter, max_steps=3)
    task_id = stack["task"]["id"]
    a = env.svc.engine
    original = a._record_tool_call
    locked = {"n": 0}

    async def locking_record(run_id, tid, step, call, sp, **kw):
        if kw.get("status") in ("executed", "error") and locked["n"] == 0:
            locked["n"] += 1
            raise OperationalError("INSERT INTO tool_calls", {}, Exception("database is locked"))
        return await original(run_id, tid, step, call, sp, **kw)

    a._record_tool_call = locking_record
    run_id = await a.claim()
    with pytest.raises(OperationalError):        # worker_loop catches this, reports worker.error, moves on
        await a.execute(run_id)
    a._record_tool_call = original
    assert calls == [{"text": "hello"}]

    b = await _takeover(env, run_id)             # lease expiry → recover → attempt+1
    await b.execute(run_id)                      # the scripted model now answers without re-requesting
    assert calls == [{"text": "hello"}], "duplicate_side_effect_count must be 0 under a DB lock"
    rows = await _rows(env.svc.db, task_id)
    async with env.svc.db.session() as s:
        status = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).scalar()
    assert {r["call_id"]: r["status"] for r in rows} == {"call_1": "interrupted"}
    # An unobserved irreversible effect with no post-state contract is an honest
    # failure, not a completion — even though the tool carries the default
    # "read" category: idempotent=False is its own admission of an effect.
    assert status == "failed"
    detail = (await env.client.get(f"/api/tasks/{task_id}")).json()
    assert "did not succeed" in str(detail.get("error") or "")
