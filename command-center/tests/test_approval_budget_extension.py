"""Owner 31.10: «Задача завершилась с ошибкой: APPROVAL_BUDGET_EXCEEDED … надо исправить в мобильном пульте».

The approval budget no longer kills the task: it parks it with ONE question (kind=budget). Approving that question
executes nothing - it only raises the budget, and the parked call has to be asked again the normal way.
Rejecting it stops the task as before. The pult never auto-answers this kind (AUTO, «не спрашивать в этом чате»)."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from bcc import approval_scope as scope
from bcc.db import tasks as tasks_t, tool_calls as tool_calls_t

from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools  # noqa: F401
from .test_v21_tool_loop import clean_registry  # noqa: F401  (autouse fixture)


async def _setup(env, calls):
    _install("terminal.run", permission="terminal.run", default_effect="ask", calls=calls)
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "python a.py", "mode": "sandbox"}),
                           ("tool", "terminal_run", {"command": "python b.py", "mode": "sandbox"}),
                           ("tool", "terminal_run", {"command": "python b.py", "mode": "sandbox"}),
                           ("text", "готово")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter, max_steps=8)
    tid = stack["task"]["id"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(meta={"approval_budget": 1}))
        await s.commit()
    return tid


async def _pending(env):
    return [a for a in (await env.client.get("/api/approvals")).json() if a["status"] == "pending"]


async def _decide(env, approval_id, approve):
    r = await env.client.post(f"/api/approvals/{approval_id}", json={"approve": approve, "by": "owner"})
    assert r.status_code == 200, r.text
    await env.svc.engine._resume_decided_approvals()   # the live watcher/sweep re-queues the parked run


async def test_budget_asks_to_extend_and_the_answer_executes_nothing(env):
    calls = []
    tid = await _setup(env, calls)
    assert await _run_task(env, tid, timeout=15) == "waiting_approval"
    first = await _pending(env)
    assert [a["kind"] for a in first] == ["tool"]
    await _decide(env, first[0]["id"], True)                       # python a.py runs

    assert await _run_task(env, tid, timeout=15) == "waiting_approval"   # NOT failed any more
    budget_q = await _pending(env)
    assert [a["kind"] for a in budget_q] == [scope.BUDGET_APPROVAL_KIND]
    assert "НЕ одобряет ни одного действия" in budget_q[0]["preview"]
    assert [c["command"] for c in calls] == ["python a.py"]
    await _decide(env, budget_q[0]["id"], True)

    assert await _run_task(env, tid, timeout=15) == "waiting_approval"
    again = await _pending(env)
    assert [a["kind"] for a in again] == ["tool"], "the parked call is asked again the normal way"
    assert [c["command"] for c in calls] == ["python a.py"], "approving the budget ran nothing"
    async with env.svc.db.session() as s:
        meta = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == tid))).scalar_one()
        parked = (await s.execute(sa.select(tool_calls_t.c.status, tool_calls_t.c.approved_by).where(
            tool_calls_t.c.approval_id == budget_q[0]["id"]))).one()
    assert meta["approval_budget"] == 1 + scope.BUDGET_EXTENSION_STEP
    assert parked[0] == "rejected" and parked[1].startswith("system:budget_approved")
    await _decide(env, again[0]["id"], True)

    assert await _run_task(env, tid, timeout=20, until=FINISHED) == "completed"
    assert [c["command"] for c in calls] == ["python a.py", "python b.py"]


async def test_declining_the_extension_stops_the_task(env):
    calls = []
    tid = await _setup(env, calls)
    assert await _run_task(env, tid, timeout=15) == "waiting_approval"
    await _decide(env, (await _pending(env))[0]["id"], True)
    assert await _run_task(env, tid, timeout=15) == "waiting_approval"
    budget_q = await _pending(env)
    await _decide(env, budget_q[0]["id"], False)
    assert await _run_task(env, tid, timeout=15, until=FINISHED) == "failed"
    t = (await env.client.get(f"/api/tasks/{tid}")).json()
    assert "APPROVAL_BUDGET_EXCEEDED" in str(t.get("error") or t["task"].get("error"))
    assert [c["command"] for c in calls] == ["python a.py"]


def test_pult_never_auto_answers_the_budget_question():
    js = (Path(__file__).resolve().parents[2] / "apps" / "bossman-mobile" / "bossmobile" / "ui" / "app.js").read_text(
        encoding="utf-8")
    assert "ev.approval_kind !== 'budget' && !cloud && !curAgentCloud()) decide(true, true)" in js
    assert "ev.approval_kind !== 'budget' && !hardAsk(ev)" in js
