"""Fable hardening — APPROVAL is authorization at effect time, not a permanent token.

Execution Truth §8: authorization must still be valid at effect time. An
approval decided while the worker was down could not be withdrawn before the
resumed run executed it. `Approvals.revoke` moves approved -> revoked; every
consumer then fails closed.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tasks as tasks_t, tool_calls as tool_calls_t

from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools


async def _rows(env, table, **where):
    async with env.svc.db.session() as s:
        stmt = sa.select(table)
        for k, v in where.items():
            stmt = stmt.where(getattr(table.c, k) == v)
        return [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]


async def test_revoked_before_resume_is_not_executed(env):
    calls: list = []
    _install("terminal.run", calls=calls, permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "git push"}), ("text", "ок")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    aid = (await _rows(env, approvals_t))[0]["id"]

    # the owner approves while no worker is running, then thinks better of it
    r = await env.client.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = await env.client.post(f"/api/approvals/{aid}/revoke", json={"by": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert r.json()["decided_by"] == "owner"

    status = await _run_task(env, task_id, until=FINISHED)
    assert calls == [], "a revoked approval must not authorize the effect"
    rows = await _rows(env, tool_calls_t, task_id=task_id)
    assert [x["status"] for x in rows] == ["rejected"]
    assert status != "completed"
    assert "отклонено" in adapter.seen_messages[1][-1]["content"]


async def test_revoke_is_only_for_approved_rows(env):
    pending = await env.svc.approvals.create("tool", "preview")
    row = await env.svc.approvals.revoke(pending["id"], by="owner")
    assert row["status"] == "pending", "a pending approval is decided, not revoked"
    rejected = await env.svc.approvals.create("tool", "preview")
    await env.svc.approvals.decide(rejected["id"], False, by="owner")
    assert (await env.svc.approvals.revoke(rejected["id"]))["status"] == "rejected"
    assert await env.svc.approvals.revoke(999_999) is None
    r = await env.client.post("/api/approvals/999999/revoke", json={"by": "owner"})
    assert r.status_code == 404


async def test_revoked_approval_cannot_be_consumed_or_used_for_override(env):
    appr = await env.svc.approvals.create("terminal", "echo hi @ /tmp")
    await env.svc.approvals.decide(appr["id"], True, by="owner")
    await env.svc.approvals.revoke(appr["id"], by="owner")
    assert await env.svc.approvals.consume(appr["id"], kind="terminal", preview="echo hi @ /tmp") is False
    assert (await _rows(env, approvals_t, id=appr["id"]))[0]["status"] == "revoked"

    from bcc.finalize import finalize_override
    task = (await env.client.post("/api/tasks", json={"title": "t", "prompt": "p"})).json()["task"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(status="waiting_approval"))
        await s.commit()
    review = await env.svc.approvals.create("review_escalation", "review", task_id=task["id"])
    await env.svc.approvals.decide(review["id"], True, by="owner")
    revoked = await env.svc.approvals.revoke(review["id"], by="owner")
    assert await finalize_override(env.svc, task["id"], approval=revoked) is False
    assert (await _rows(env, tasks_t, id=task["id"]))[0]["status"] == "waiting_approval"


async def test_revocation_is_journaled_on_the_bus(env):
    appr = await env.svc.approvals.create("tool", "x")
    await env.svc.approvals.decide(appr["id"], True, by="owner")
    await env.svc.approvals.revoke(appr["id"], by="owner")
    kinds = [e.get("kind") for e in await env.svc.bus.recent(50)]
    assert "approval.revoked" in kinds and "approval.decided" in kinds
