"""P0 replay: `waiting_approval` must never outlive its decision (INV-RD-1).

Source of truth for these cases is the 202-event acceptance corpus vendored at
`docs/testing/acceptance-run-20260906/traces-20260908/tasks-trace.jsonl`. Four
of its tasks ended the same way — task status `waiting_approval`,
`pending_approvals: 0`, checkpoint note `review_escalated`, retry refused with
`TASK_STATE_CONFLICT`, and `/stop` the only escape:

  T1 run2  "run2 deadlocked in waiting_approval with 0 pending approvals"
  T1 run3  same shape after a second attempt
  T2 run4  phantom obligation `file:example.com` → review FAIL ×2 → escalation
  T3       three false-negative reviews of an existing, correct report

The tests below reproduce each mechanism against the live engine and assert the
system now reaches a *decision*. They deliberately do NOT assert that any task
completes: the evidence gates are unchanged and still refuse. What changed is
that refusal is now terminal-and-honest instead of silent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import approvals as approvals_t, settings_kv, tasks as tasks_t, task_runs as runs_t, utcnow
from bcc import review_escalation as resc
from bcc.features import review_gate

from .helpers import make_stack
from .test_finalize_gate import _allow_root, _set_meta, _status, _run_once
from .conftest import FakeAdapter

TRACE = (Path(__file__).resolve().parents[2] / "docs" / "testing" /
         "acceptance-run-20260906" / "traces-20260908" / "tasks-trace.jsonl")


async def _park(env, task_id, run_id, *, kind="review_escalation", status="pending"):
    """Put the task in the exact shape the corpus recorded."""
    async with env.svc.db.session() as s:
        aid = (await s.execute(sa.insert(approvals_t).values(
            task_id=task_id, run_id=run_id, kind=kind, status=status,
            preview="review escalated", created_at=utcnow(),
            decided_by="owner" if status != "pending" else None,
            decided_at=utcnow() if status != "pending" else None))).inserted_primary_key[0]
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            status="waiting_approval", updated_at=utcnow()))
        await s.commit()
    return int(aid)


async def _age(env, task_id, seconds=600):
    """Move the task out of the transition grace window without sleeping."""
    from datetime import timedelta
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            updated_at=utcnow() - timedelta(seconds=seconds)))
        await s.commit()


async def _pending(env, task_id):
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(approvals_t.c.id).where(
            approvals_t.c.task_id == task_id, approvals_t.c.status == "pending"))).fetchall()
    return [int(r[0]) for r in rows]


async def _stack(env, tmp_path, *, prompt="проверка"):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    stack = await make_stack(env.client, prompt=prompt)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    return tid, run


# --------------------------------------------------------------------------
# The corpus itself
# --------------------------------------------------------------------------

def test_corpus_is_vendored_and_still_shows_four_deadlocks():
    """The regression's premise, not decoration: if the corpus stops recording
    the pathology, these tests are asserting nothing."""
    assert TRACE.exists(), f"golden corpus missing: {TRACE}"
    rows = [json.loads(line) for line in TRACE.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert len(rows) == 202
    text = json.dumps(rows, ensure_ascii=False)
    assert "waiting_approval" in text and "0 pending approvals" in text


# --------------------------------------------------------------------------
# D1 — the owner said NO and nobody read it (T1/T3 shape)
# --------------------------------------------------------------------------

async def test_rejected_escalation_is_a_decision_not_a_park(env, tmp_path):
    """`_tick` used to select only `status='approved'`. A rejection therefore
    settled nothing: the task stayed `waiting_approval` behind a decided row,
    with zero pending approvals — exactly the corpus shape."""
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run, status="rejected")
    assert await _pending(env, tid) == []
    assert await resc.live_decision(env.svc, tid) is None      # deadlocked before the fix

    await review_gate._tick(env.svc)

    assert await _status(env, tid) == "failed"
    async with env.svc.db.session() as s:
        run_row = (await s.execute(sa.select(runs_t.c.status, runs_t.c.error).where(
            runs_t.c.id == run))).first()
    assert run_row._mapping["status"] == "failed"              # both projections closed
    assert "REVIEW_ESCALATION_REJECTED" in (run_row._mapping["error"] or "")


# --------------------------------------------------------------------------
# D2 — approved, override refuses, the rename already burned the only object
# --------------------------------------------------------------------------

async def test_approved_escalation_whose_override_refuses_re_asks_the_owner(env, tmp_path):
    """T2's mechanism. The declared effect is genuinely absent, so
    `finalize_override` refuses — correctly, and it still does. The defect was
    that the refusal left nothing pending. Now the owner is asked again, with
    the reason, inside a bounded budget."""
    tid, run = await _stack(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [
        {"kind": "file", "target": str(tmp_path / "absent.txt"), "expect": {"exists": True}}]})
    await _park(env, tid, run, status="approved")

    await review_gate._tick(env.svc)

    assert await _status(env, tid) == "waiting_approval"       # evidence gate unchanged
    pending = await _pending(env, tid)
    assert len(pending) == 1, "a refused override must leave a live decision object"
    async with env.svc.db.session() as s:
        preview = (await s.execute(sa.select(approvals_t.c.preview).where(
            approvals_t.c.id == pending[0]))).scalar()
    assert "absent.txt" in preview or "not verified" in preview or "круг 1" in preview


async def test_refused_override_never_completes_the_task(env, tmp_path):
    """Negative control for the fix above: re-asking must not become a way to
    finalize a task whose declared effect is still missing."""
    tid, run = await _stack(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [
        {"kind": "file", "target": str(tmp_path / "absent.txt"), "expect": {"exists": True}}]})
    await _park(env, tid, run, status="approved")
    for _ in range(6):
        await _age(env, tid)
        async with env.svc.db.session() as s:
            await s.execute(sa.update(approvals_t).where(
                approvals_t.c.task_id == tid, approvals_t.c.status == "pending").values(
                status="approved", decided_by="owner", decided_at=utcnow()))
            await s.commit()
        await review_gate._tick(env.svc)
        assert await _status(env, tid) != "completed"
    assert await _status(env, tid) == "failed"                  # bounded, not endless


# --------------------------------------------------------------------------
# D3 — the general invariant sweep
# --------------------------------------------------------------------------

async def test_sweep_recovers_a_task_parked_behind_nothing(env, tmp_path):
    """Whatever consumed, deleted or renamed the approval, a task parked behind
    nothing is deadlocked. The sweep is the backstop that makes
    ReviewDeadlockRate = 0 hold for paths not enumerated above."""
    tid, run = await _stack(env, tmp_path)
    aid = await _park(env, tid, run)
    async with env.svc.db.session() as s:                      # simulate a consumed/lost decision
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.id == aid))
        await s.commit()
    await _age(env, tid)
    assert await resc.live_decision(env.svc, tid) is None

    acted = await resc.reconcile(env.svc)

    assert [a["action"] for a in acted] == ["reopened"]
    assert len(await _pending(env, tid)) == 1
    assert await resc.live_decision(env.svc, tid) == "pending_approval"


async def test_sweep_fails_honestly_once_the_budget_is_spent(env, tmp_path):
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == tid))
        await s.commit()
    await _set_meta(env, tid, {resc.ROUNDS_META_KEY: resc.DEFAULT_MAX_ROUNDS,
                               resc.LAST_REASON_META_KEY: "required effects not verified"})
    await _age(env, tid)

    acted = await resc.reconcile(env.svc)

    assert [a["action"] for a in acted] == ["failed"]
    assert await _status(env, tid) == "failed"
    async with env.svc.db.session() as s:
        err = (await s.execute(sa.select(runs_t.c.error).where(runs_t.c.id == run))).scalar()
    assert "REVIEW_ESCALATION_EXHAUSTED" in (err or "")
    assert "required effects not verified" in (err or "")      # the honest reason survives


# --------------------------------------------------------------------------
# Negative controls — the sweep must not touch healthy tasks
# --------------------------------------------------------------------------

async def test_sweep_leaves_a_task_with_a_pending_approval_alone(env, tmp_path):
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    await _age(env, tid)
    assert await resc.reconcile(env.svc) == []
    assert await _status(env, tid) == "waiting_approval"


async def test_sweep_leaves_a_task_inside_the_grace_window_alone(env, tmp_path):
    """The writers create the approval before flipping the status, so a task
    parked one millisecond ago is mid-transition, not deadlocked."""
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == tid))
        await s.commit()
    assert await resc.reconcile(env.svc) == []                 # updated_at is now
    assert await _status(env, tid) == "waiting_approval"


async def test_sweep_leaves_a_task_parked_on_a_tool_approval_alone(env, tmp_path):
    """A parked tool call is a live decision object even though its approval row
    has already been decided — `engine._resume_decided_approvals` owns it."""
    from types import SimpleNamespace
    from bcc.tools import REGISTRY
    tid, run = await _stack(env, tmp_path)
    aid = await _park(env, tid, run, kind="tool", status="approved")
    await env.svc.engine._record_tool_call(
        run, tid, 0, SimpleNamespace(id="c1", name="terminal.run", arguments={"command": "ls"}),
        REGISTRY.get("terminal.run"), effect="ask", status="pending_approval", approval_id=aid)
    await _age(env, tid)
    assert await resc.live_decision(env.svc, tid) == "tool_call_pending"
    assert await resc.reconcile(env.svc) == []


@pytest.mark.parametrize("closed", ["completed", "failed", "stopped", "cancelled"])
async def test_sweep_never_touches_a_closed_task(env, tmp_path, closed):
    tid, run = await _stack(env, tmp_path)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(status=closed))
        await s.commit()
    assert await resc.reconcile(env.svc) == []
    assert await _status(env, tid) == closed


# --------------------------------------------------------------------------
# The metric the run is graded on
# --------------------------------------------------------------------------

async def test_audit_reports_zero_deadlocks_after_a_sweep(env, tmp_path):
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == tid))
        await s.commit()
    await _age(env, tid)
    assert (await resc.audit(env.svc))["deadlocked"] == 1
    await resc.reconcile(env.svc)
    assert (await resc.audit(env.svc))["deadlocked"] == 0


async def test_budget_is_owner_configurable(env, tmp_path):
    """Budgets must be settings, not hard-coded constants (master §8): a complex
    mission may legitimately need more rounds than a doc edit."""
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(
            key=resc.MAX_ROUNDS_KEY, value_enc=env.svc.vault.encrypt("0")))
        await s.commit()
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == tid))
        await s.commit()
    await _age(env, tid)
    acted = await resc.reconcile(env.svc)
    assert [a["action"] for a in acted] == ["failed"]           # budget 0 → no re-ask
