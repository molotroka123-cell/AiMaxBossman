"""Astra/Codex F6 (2026-09-08, 2/2, 24 persisted transitions): recovery must be
bounded at the RUN level, not per failure-class bucket.

`Ladder.from_dict` gave a run a fresh ladder whenever the failure class
changed, so alternating "unsupported tool use" / "empty response" re-earned the
degraded path forever: 12 handlings, 12 `queued`, spent=['degraded_path'] on
every one. Now spent rungs persist across classes and a global transition
budget (`max_retries + MAX_STRATEGY_CHANGES`) holds whatever the labels do.

    No finite mission can generate an unbounded sequence of recovery attempts
    by alternating error labels.
"""
from __future__ import annotations

import itertools

import pytest
import sqlalchemy as sa

from bcc import model_health as mh
from bcc.db import task_runs, tasks
from bcc.reality import recovery as rec

from .helpers import make_stack


# ------------------------------------------------------------- pure ladder

def drive(errors, *, max_retries=0, fallback=7, healthy=(), current=1, saved=None):
    """Run the failure handler's ladder logic over a sequence of error strings
    exactly as engine._handle_failure does, returning the rung names."""
    rungs = []
    attempt = 0
    for error in errors:
        cls = rec.classify_failure(error)
        ladder = rec.Ladder.from_dict(saved, cls)
        rung = rec.next_rung(ladder, current_model_id=current, fallback_model_id=fallback,
                             healthy_models=healthy, retries_left=max(0, max_retries - attempt),
                             max_retries=max_retries)
        rungs.append(rung.name)
        saved = rung.ladder.to_dict()
        attempt += 1
        if rung.terminal:
            break
    return rungs, saved


def test_the_same_error_repeatedly_terminates():
    rungs, _ = drive(["empty response no content"] * 20)
    assert rungs[-1] == rec.HUMAN and len(rungs) <= 3


def test_alternating_two_classes_terminates_with_the_degraded_path_spent_once():
    errors = ["unsupported tool use", "empty response no content"] * 10
    rungs, saved = drive(errors)
    assert rungs[-1] == rec.HUMAN
    assert rungs.count(rec.DEGRADED_PATH) == 1
    assert rungs.count(rec.ALTERNATE_MODEL) == 1
    assert len(rungs) <= rec.recovery_budget(0) + 1
    assert saved["classes"][:2] == [rec.CAPABILITY, rec.SILENT]


@pytest.mark.parametrize("n", [3, 5, 7])
def test_rotating_n_classes_terminates_within_the_budget(n):
    pool = ["unsupported tool use", "empty response no content", "connection reset",
            "429 rate limit", "context length exceeded", "something odd", "no endpoints found"]
    errors = list(itertools.islice(itertools.cycle(pool[:n]), 40))
    rungs, _ = drive(errors, max_retries=2)
    assert rungs[-1] == rec.HUMAN
    assert len(rungs) <= rec.recovery_budget(2) + 1


def test_the_owner_retry_budget_is_still_honoured_for_a_flaky_provider():
    rungs, _ = drive(["connection reset"] * 10, max_retries=3, fallback=None)
    assert rungs[:3] == [rec.RETRY_SAME] * 3
    assert rungs[-1] == rec.HUMAN and len(rungs) <= rec.recovery_budget(3) + 1


def test_a_provider_fallback_is_taken_once_across_classes():
    rungs, _ = drive(["429 rate limit", "unsupported tool use", "429 rate limit"], fallback=9)
    assert rungs.count(rec.ALTERNATE_MODEL) == 1


def test_a_restart_during_recovery_keeps_the_spent_history():
    rungs, saved = drive(["unsupported tool use"])
    assert rungs == [rec.ALTERNATE_MODEL]
    # a checkpoint round-trip (restart) loses nothing
    restored = rec.Ladder.from_dict(dict(saved), rec.SILENT)
    assert rec.ALTERNATE_MODEL in restored.spent and restored.transitions == 2
    rungs2, _ = drive(["empty response no content", "unsupported tool use"], saved=saved)
    assert rungs2 == [rec.DEGRADED_PATH, rec.HUMAN]


def test_a_legacy_checkpoint_without_the_new_fields_is_read_conservatively():
    old = {"failure_class": "silent", "spent": ["degraded_path"]}
    ladder = rec.Ladder.from_dict(old, "capability")
    assert ladder.spent == ("degraded_path",) and ladder.transitions == 1
    assert ladder.classes == ("silent", "capability")


def test_no_rung_grants_authority():
    """Changing model or degrading changes HOW, never WHAT is permitted."""
    for rung_name in (rec.ALTERNATE_MODEL, rec.DEGRADED_PATH, rec.RETRY_SAME, rec.HUMAN):
        rung = rec.Rung(rung_name, "r", rec.Ladder("silent"))
        d = rung.to_dict()
        assert not any(k in d for k in ("permissions", "allowed_tools", "approval", "grant"))


def test_the_budget_is_finite_for_every_max_retries():
    for max_retries in range(0, 6):
        assert rec.recovery_budget(max_retries) == max_retries + rec.MAX_STRATEGY_CHANGES


# ------------------------------------------------------- the engine's path

async def _run_row(env, run_id):
    async with env.svc.db.session() as s:
        return (await s.execute(sa.select(task_runs).where(task_runs.c.id == run_id))).mappings().one()


@pytest.mark.parametrize("errors", [
    ["unsupported tool use", "empty response no content"] * 6,             # Astra F6
    ["empty response no content"] * 12,
    ["unsupported tool use", "connection reset", "429 rate limit", "something odd"] * 3,
])
async def test_the_production_failure_handler_reaches_a_terminal_state(env, errors):
    stack = await make_stack(env.client, max_retries=0)
    task = stack["task"]
    run_id = await env.svc.engine.claim()
    states = []
    for error in errors:
        await env.svc.engine._handle_failure(run_id, task, error, [], 0)
        row = await _run_row(env, run_id)
        states.append((row["status"], row["attempt"], (row["checkpoint"] or {}).get("recovery_ladder")))
        if row["status"] == "failed":
            break
    assert states[-1][0] == "failed", states
    assert len(states) <= rec.recovery_budget(0) + 1
    async with env.svc.db.session() as s:
        t = (await s.execute(sa.select(tasks.c.status).where(tasks.c.id == task["id"]))).scalar_one()
    assert t == "failed"
    assert "recovery_exhausted" in (states[-1][2] and (await _run_row(env, run_id))["checkpoint"].get("note", ""))


async def test_a_stopped_task_is_not_requeued_by_a_late_failure(env):
    stack = await make_stack(env.client, max_retries=3)
    task = stack["task"]
    run_id = await env.svc.engine.claim()
    assert (await env.client.post(f"/api/tasks/{task['id']}/stop")).status_code == 200
    await env.svc.engine._handle_failure(run_id, task, "connection reset", [], 0)
    row = await _run_row(env, run_id)
    assert row["status"] == "stopped"
    async with env.svc.db.session() as s:
        t = (await s.execute(sa.select(tasks.c.status).where(tasks.c.id == task["id"]))).scalar_one()
    assert t == "stopped"


async def test_a_paused_task_stays_paused_while_its_run_waits(env):
    stack = await make_stack(env.client, max_retries=3)
    task = stack["task"]
    run_id = await env.svc.engine.claim()
    assert (await env.client.post(f"/api/tasks/{task['id']}/pause")).status_code == 200
    await env.svc.engine._handle_failure(run_id, task, "connection reset", [], 0)
    row = await _run_row(env, run_id)
    assert row["status"] == "queued"
    async with env.svc.db.session() as s:
        t = (await s.execute(sa.select(tasks.c.status).where(tasks.c.id == task["id"]))).scalar_one()
    assert t == "paused"
