"""P0 — `waiting_approval` may never outlive the decision it waits for.

INVARIANT (INV-RD-1): a task parked in `waiting_approval` is legal only while
at least one *live decision object* exists for it:

  * an approval row for the task with `status='pending'` (the owner can act), or
  * a `review_escalation` approval already `approved` but not yet swept by
    `features/review_gate._tick` (that sweep will act), or
  * a `tool_calls` row still `pending_approval` (engine.on_approval_decided /
    _resume_decided_approvals will act).

When none of those hold, nothing in the system can move the task: a
`waiting_approval` task is not `queued`, so no worker claims it, and there is
nothing for the owner to decide. The 2026-09-07 acceptance corpus
(`docs/testing/acceptance-run-20260906/traces-20260908/tasks-trace.jsonl`,
202 events) hit exactly this four times — `waiting_approval` with
`pending_approvals: 0` — and `/stop` was the only escape.

Three concrete paths produced it:

  D1  the owner REJECTS a `review_escalation`. `_tick` only ever selected
      `status='approved'`, so a rejection was read by nobody and the task
      stayed parked behind a decided approval.
  D2  the owner APPROVES, `_tick` renames the row to `review_escalation_done`
      *before* calling `finalize_override`, and the override refuses (an absent
      or phantom obligation, stale evidence, a tool call still pending). The
      rename already burned the only live object; the refusal produced silence.
  D3  any other transition into `waiting_approval` whose approval is later
      decided, consumed or deleted without releasing the task.

This module supplies the terminating half of the state machine. It does NOT
weaken a gate: `finalize_override` still demands fresh verification of every
declared effect, and nothing here can write `completed`. The only change is
that a refusal now yields a *decision* — re-ask the owner while the escalation
budget allows, otherwise fail honestly with the reason — instead of silence.

Budget and grace are owner-configurable through `settings`; the defaults are
deliberately small but non-zero, because a run that re-asks forever is the
approval storm this same convergence run is closing.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from .db import (approvals as approvals_t, settings_kv, tasks as tasks_t,
                 task_runs as runs_t, tool_calls as tool_calls_t, utcnow)

#: Escalation rounds a single task may spend before the deadlock is reported as
#: a failure. Each round is one owner question, so this is also an approval
#: ceiling for the review path.
DEFAULT_MAX_ROUNDS = 3
#: A task is only judged deadlocked once it has been parked this long. The
#: writers always create the approval *before* flipping the task status, so a
#: shorter window would only ever catch an in-flight transition, never a real
#: deadlock.
DEFAULT_GRACE_SECONDS = 30

MAX_ROUNDS_KEY = "review.max_escalation_rounds"
GRACE_KEY = "review.deadlock_grace_seconds"

#: `_tick` renames a settled escalation so it cannot re-fire. Both names are
#: settled: neither is a live decision object.
SETTLED_KINDS = ("review_escalation_done", "review_escalation_refused")

ROUNDS_META_KEY = "review_escalation_rounds"
LAST_REASON_META_KEY = "review_escalation_last_reason"

#: Statuses whose task must never be revived by this sweep.
CLOSED_TASK_STATUSES = ("completed", "failed", "stopped", "cancelled")
#: Run statuses that are already closed — everything else is still open and is
#: closed alongside the task, so a failed task can never leave a live run behind.
TERMINAL_RUN_STATUSES = ("completed", "failed", "stopped", "cancelled", "blocked")


async def _int_setting(svc, key: str, default: int) -> int:
    """Owner-configurable integer. Unset/​unreadable settings keep the default:
    a malformed value must not disable the deadlock sweep."""
    try:
        async with svc.db.session() as s:
            row = (await s.execute(sa.select(settings_kv.c.value_enc)
                                   .where(settings_kv.c.key == key))).first()
        if row is None or row[0] is None:
            return default
        raw = svc.vault.decrypt(row[0]) if getattr(svc, "vault", None) else row[0]
        value = int(str(raw).strip())
    except Exception:  # noqa: BLE001 — a broken setting is not a reason to stop sweeping
        return default
    return value if value >= 0 else default


async def live_decision(svc, task_id: int) -> str | None:
    """Name of the live decision object for `task_id`, or None if the task is
    parked behind nothing.

    Kept deliberately generous: every state another component is still able to
    act on counts as live, so the sweep can only fire on a task that is
    genuinely stuck. A false negative here would fail a healthy task."""
    async with svc.db.session() as s:
        pending = (await s.execute(sa.select(approvals_t.c.id).where(
            approvals_t.c.task_id == task_id,
            approvals_t.c.status == "pending").limit(1))).first()
        if pending is not None:
            return "pending_approval"
        unswept = (await s.execute(sa.select(approvals_t.c.id).where(
            approvals_t.c.task_id == task_id,
            approvals_t.c.kind == "review_escalation",
            approvals_t.c.status == "approved").limit(1))).first()
        if unswept is not None:
            return "escalation_approved_unswept"
        parked_call = (await s.execute(sa.select(tool_calls_t.c.id).where(
            tool_calls_t.c.task_id == task_id,
            tool_calls_t.c.status == "pending_approval").limit(1))).first()
        if parked_call is not None:
            return "tool_call_pending"
    return None


async def _task_meta(svc, task_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(
            tasks_t.c.id == task_id))).first()
    meta = row._mapping["meta"] if row is not None else None
    return dict(meta) if isinstance(meta, dict) else {}


async def _write_meta(svc, task_id: int, meta: dict) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta))
        await s.commit()


async def _latest_run(svc, task_id: int) -> int | None:
    async with svc.db.session() as s:
        return (await s.execute(sa.select(runs_t.c.id).where(
            runs_t.c.task_id == task_id).order_by(runs_t.c.id.desc()).limit(1))).scalar()


async def _last_settled_reason(svc, task_id: int) -> str:
    """The most recent settled escalation's preview — carries the refusal reason
    written by `record_refusal`, so the next question to the owner says what
    actually blocked the previous one instead of repeating the first ask."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(approvals_t.c.preview).where(
            approvals_t.c.task_id == task_id,
            approvals_t.c.kind.in_(SETTLED_KINDS)).order_by(
            approvals_t.c.id.desc()).limit(1))).first()
    return str(row[0] or "") if row is not None else ""


async def record_refusal(svc, approval_id: int, task_id: int, reason: str) -> dict:
    """Settle an escalation the owner approved but `finalize_override` refused,
    then immediately advance the state machine.

    The row is renamed so `_tick` cannot re-fire on it, and the reason is kept
    in both the approval preview and the task meta so the next round — and the
    owner — can see WHY the approval did not finalize the task.

    The advance is NOT left to the periodic sweep: a refusal is a *known*
    settlement, so the grace window (which exists only to avoid racing an
    in-flight transition) does not apply. Waiting for the sweep would leave the
    task provably deadlocked for up to `grace_seconds` — the very state this
    module exists to make impossible."""
    text = str(reason or "").strip() or "finalize refused without a reason"
    async with svc.db.session() as s:
        await s.execute(sa.update(approvals_t).where(approvals_t.c.id == approval_id).values(
            kind="review_escalation_refused",
            preview=sa.func.substr(
                sa.func.coalesce(approvals_t.c.preview, "") + f"\n[finalize refused] {text}", 1, 4000)))
        await s.commit()
    meta = await _task_meta(svc, task_id)
    meta[LAST_REASON_META_KEY] = text[:500]
    await _write_meta(svc, task_id, meta)
    await svc.bus.emit("review.escalation_refused", task_id=task_id,
                       approval_id=approval_id, reason=text[:500])
    return await advance(svc, task_id)


async def advance(svc, task_id: int, *, max_rounds: int | None = None) -> dict:
    """One step of the escalation state machine for a task parked behind nothing.

    Either re-asks the owner (budget remaining) or fails the task with the
    honest reason. Never completes anything, never touches a task that still
    has a live decision object or is already closed."""
    if max_rounds is None:
        max_rounds = await _int_setting(svc, MAX_ROUNDS_KEY, DEFAULT_MAX_ROUNDS)
    async with svc.db.session() as s:
        status = (await s.execute(sa.select(tasks_t.c.status).where(
            tasks_t.c.id == task_id))).scalar_one_or_none()
    if status != "waiting_approval":
        return {"task_id": task_id, "action": "skipped", "reason": f"status={status}"}
    if await live_decision(svc, task_id) is not None:
        return {"task_id": task_id, "action": "skipped", "reason": "live decision exists"}
    meta = await _task_meta(svc, task_id)
    rounds = int(meta.get(ROUNDS_META_KEY) or 0)
    run_id = await _latest_run(svc, task_id)
    previous = str(meta.get(LAST_REASON_META_KEY) or "") or await _last_settled_reason(svc, task_id)
    if rounds < max_rounds:
        approval_id = await _reopen(svc, task_id, run_id, rounds + 1, max_rounds, previous)
        meta[ROUNDS_META_KEY] = rounds + 1
        await _write_meta(svc, task_id, meta)
        await svc.bus.emit("review.deadlock_recovered", task_id=task_id, run_id=run_id,
                           round=rounds + 1, max_rounds=max_rounds, approval_id=approval_id)
        return {"task_id": task_id, "action": "reopened", "round": rounds + 1,
                "approval_id": approval_id}
    error = (f"REVIEW_ESCALATION_EXHAUSTED: задача {max_rounds} раз уходила на решение "
             f"владельца и ни разу не смогла завершиться по свежим доказательствам."
             + (f" Последняя причина: {previous.strip()[:400]}" if previous.strip() else ""))
    if await _fail_task(svc, task_id, run_id, error):
        await svc.bus.emit("review.deadlock_failed", task_id=task_id, run_id=run_id,
                           rounds=rounds, reason=error[:500])
        return {"task_id": task_id, "action": "failed", "round": rounds}
    return {"task_id": task_id, "action": "skipped", "reason": "task moved concurrently"}


async def settle_rejection(svc, approval_id: int, task_id: int) -> bool:
    """D1: the owner said NO to a review escalation.

    A rejected escalation is a decision, not a pause: the reviewer's veto stands
    and the task failed review. Left unread — as it was — the task stayed parked
    behind an approval nobody would ever look at again."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(approvals_t).where(
            approvals_t.c.id == approval_id))).first()
        if row is None:
            return False
        appr = dict(row._mapping)
        if appr.get("status") != "rejected" or appr.get("kind") != "review_escalation":
            return False
        await s.execute(sa.update(approvals_t).where(approvals_t.c.id == approval_id).values(
            kind="review_escalation_refused"))
        await s.commit()
    return await _fail_task(
        svc, task_id, appr.get("run_id"),
        "REVIEW_ESCALATION_REJECTED: владелец отклонил эскалацию ревью; "
        "задача не считается выполненной")


async def _fail_task(svc, task_id: int, run_id: int | None, error: str) -> bool:
    """Close both projections under a CAS on `waiting_approval`.

    Deliberately not `engine._finish`: that path asserts the caller holds the
    run's in-memory fence, which a background reconciler never does. The CAS on
    the task status gives the same "one writer wins" guarantee, and the run is
    only closed while it is still the parked `queued` row."""
    async with svc.db.session() as s:
        changed = await s.execute(sa.update(tasks_t).where(
            tasks_t.c.id == task_id,
            tasks_t.c.status == "waiting_approval").values(
            status="failed", updated_at=utcnow()))
        if not changed.rowcount:
            await s.rollback()
            return False
        if run_id is not None:
            # Any non-terminal run status counts: a parked escalation leaves the
            # run `queued`, but a task can also be swept while its run is still
            # `leased`/`running` after a crash. Listing the terminal statuses to
            # exclude is the safe direction — a status this code has not heard of
            # is closed rather than left open forever.
            await s.execute(sa.update(runs_t).where(
                runs_t.c.id == run_id,
                sa.not_(runs_t.c.status.in_(TERMINAL_RUN_STATUSES))).values(
                status="failed", error=error[:2000], finished_at=utcnow(),
                worker_lease_until=None))
        await s.commit()
    await svc.bus.emit("task.failed", task_id=task_id, run_id=run_id, error=error[:500])
    return True


async def _reopen(svc, task_id: int, run_id: int | None, round_no: int,
                  max_rounds: int, previous: str) -> int | None:
    """Ask the owner again, once, with the reason the last round did not settle."""
    tail = f"\nПричина прошлого отказа: {previous.strip()[-600:]}" if previous.strip() else ""
    preview = (
        f"Задача {task_id} осталась в waiting_approval без действующего подтверждения "
        f"(круг {round_no}/{max_rounds}). Решение по прошлой эскалации уже принято, "
        f"но завершить задачу оно не позволило.{tail}\n"
        "Подтвердите, чтобы Bossman ещё раз попытался закрыть задачу по свежим "
        "доказательствам, или отклоните — тогда задача будет закрыта как невыполненная.")
    appr = await svc.approvals.create(kind="review_escalation", preview=preview,
                                      task_id=task_id, run_id=run_id)
    return (appr or {}).get("id")


async def reconcile(svc, *, grace_seconds: int | None = None,
                    max_rounds: int | None = None) -> list[dict]:
    """Find `waiting_approval` tasks with no live decision object and settle them.

    Returns one record per task acted on. Never writes `completed`: the only
    outcomes are "ask the owner again" and "fail with the honest reason"."""
    if max_rounds is None:
        max_rounds = await _int_setting(svc, MAX_ROUNDS_KEY, DEFAULT_MAX_ROUNDS)
    if grace_seconds is None:
        grace_seconds = await _int_setting(svc, GRACE_KEY, DEFAULT_GRACE_SECONDS)
    cutoff = utcnow() - timedelta(seconds=max(0, int(grace_seconds)))
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(tasks_t.c.id, tasks_t.c.updated_at).where(
            tasks_t.c.status == "waiting_approval"))).fetchall()
    acted: list[dict] = []
    for row in rows:
        task_id = int(row[0])
        updated_at = row[1]
        if updated_at is not None and updated_at > cutoff:
            continue                      # still inside the transition window
        if await live_decision(svc, task_id) is not None:
            continue                      # somebody can still act on this task
        result = await advance(svc, task_id, max_rounds=max_rounds)
        if result.get("action") != "skipped":
            acted.append(result)
    return acted


async def audit(svc) -> dict[str, Any]:
    """Owner-visible deadlock report. `deadlocked` is the metric the convergence
    run pins at zero (`ReviewDeadlockRate = 0`)."""
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(tasks_t.c.id).where(
            tasks_t.c.status == "waiting_approval"))).fetchall()
    waiting = [int(r[0]) for r in rows]
    deadlocked = [tid for tid in waiting if await live_decision(svc, tid) is None]
    return {"waiting_approval": len(waiting), "deadlocked": len(deadlocked),
            "deadlocked_task_ids": deadlocked,
            "max_rounds": await _int_setting(svc, MAX_ROUNDS_KEY, DEFAULT_MAX_ROUNDS),
            "grace_seconds": await _int_setting(svc, GRACE_KEY, DEFAULT_GRACE_SECONDS)}
