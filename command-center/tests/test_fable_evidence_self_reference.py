"""Fable hardening — executor bookkeeping is never post-state evidence.

TOOL_SUCCESS != VERIFIED_EFFECT and APPROVAL != POST_STATE. A `db` obligation
pointed at `tool_calls`, `task_runs`, `tasks` or `approvals` would let a run
prove its effect by reading its own receipt, its own run row, its own task row
or the approval that merely authorized it. The verifier reads only world-state
tables; execution bookkeeping is refused as an observation target.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t
from bcc.v2.verification import ExpectedState, verify

from .conftest import FakeAdapter
from .helpers import make_stack


async def _run_once(env):
    for _ in range(6):
        run_id = await env.svc.engine.claim()
        if run_id is None:
            break
        await env.svc.engine.execute(run_id)


async def _status(env, task_id):
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


@pytest.mark.parametrize("table", ["tool_calls", "task_runs", "tasks", "approvals"])
async def test_bookkeeping_tables_are_not_observable_post_state(env, table):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    res = await verify(ExpectedState("db", table, {"where": {"id": tid}}), svc=env.svc, task={"id": tid})
    assert res.status == "UNVERIFIED", (table, res.reason)
    assert "bookkeeping" in res.reason


async def test_task_cannot_finalize_by_pointing_evidence_at_its_own_row(env):
    """The task row exists by construction; it must never count as a verified effect."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово, всё сделано")
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(meta={
            "required_effects": [{"kind": "db", "target": "tasks", "expect": {"where": {"id": tid}}}]}))
        await s.commit()
    await _run_once(env)
    assert await _status(env, tid) != "completed"
    events = await env.svc.bus.recent(200)
    assert not any(e.get("kind") == "task.finalized" for e in events)


async def test_world_state_table_is_still_observable(env):
    """Narrowing the allowlist must not break genuine world-state readback (facts)."""
    from bcc.db import facts as facts_t, utcnow
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(facts_t).values(subject="fable", predicate="checks", object="db",
                                                  statement="fable checks db", valid_at=utcnow(),
                                                  created_at=utcnow()))
        await s.commit()
    res = await verify(ExpectedState("db", "facts", {"where": {"subject": "fable"}, "equals": {"object": "db"}}),
                       svc=env.svc, task={"id": 0})
    assert res.status == "VERIFIED", res.reason
