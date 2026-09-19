"""§8 — a simple task cannot cost a million tokens.

The corpus recorded 1 282 044 input tokens and $4.04 for a two-line
documentation fix (T3), and 432 257 tokens for reading one web page (T2).
Nothing raised an error: the run had no ceiling and no way to notice it was
circling.

Each guard here is paired with a negative control, because the failure mode of
a budget is not "too loose" — it is "tight enough to truncate real work and
call the truncation a success".
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from bcc import mission_budget as mb
from bcc.db import (approvals as approvals_t, interventions as interventions_t,
                    tasks as tasks_t, task_runs as runs_t, tool_calls as tool_calls_t)
from bcc.providers import ChatResult

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools


# ------------------------------------------------------------------- limits

def test_defaults_are_generous_enough_for_real_work_and_tight_enough_to_catch_the_pathology():
    """The number that matters: the defaults must sit between the largest
    legitimate run in the corpus and the pathology they exist to stop."""
    limits = mb.Limits.for_task(None)
    assert limits.max_tokens > 154_405          # T2's first legitimate run
    assert limits.max_tokens < 1_282_044        # T3's pathology
    assert 0 < limits.max_cost_usd < 4.04       # T3 spent $4.04 on a doc edit


def test_limits_are_per_task_and_configurable():
    task = {"meta": {mb.TOKENS_KEY: 1000, mb.COST_KEY: 0.5,
                     mb.IDENTICAL_KEY: 2, mb.STALLED_KEY: 1}}
    limits = mb.Limits.for_task(task)
    assert (limits.max_tokens, limits.max_cost_usd) == (1000, 0.5)
    assert (limits.max_identical_calls, limits.max_stalled_steps) == (2, 1)


def test_zero_disables_a_guard_but_nonsense_does_not():
    """0 is an explicit owner choice. A typo must fall back to the default, not
    silently remove the ceiling."""
    assert mb.Limits.for_task({"meta": {mb.TOKENS_KEY: 0}}).max_tokens == 0
    assert mb.Limits.for_task({"meta": {mb.TOKENS_KEY: "лимит"}}).max_tokens == mb.DEFAULT_MAX_TOKENS
    assert mb.Limits.for_task({"meta": {mb.TOKENS_KEY: -1}}).max_tokens == mb.DEFAULT_MAX_TOKENS
    assert mb.Limits.for_task({"meta": None}).max_tokens == mb.DEFAULT_MAX_TOKENS


# --------------------------------------------------------------- spend guard

def test_token_ceiling_trips_only_above_the_limit():
    limits = mb.Limits.for_task({"meta": {mb.TOKENS_KEY: 100, mb.COST_KEY: 1.0}})
    assert mb.check_spend(limits, tokens_in=60, tokens_out=40, cost_usd=0.1) is None
    breach = mb.check_spend(limits, tokens_in=60, tokens_out=41, cost_usd=0.1)
    assert breach is not None and breach.code == "TOKEN_BUDGET_EXCEEDED"
    assert "101" in breach.detail and "100" in breach.detail       # both numbers reported


def test_cost_ceiling_is_independent_of_the_token_ceiling():
    limits = mb.Limits.for_task({"meta": {mb.TOKENS_KEY: 10 ** 9, mb.COST_KEY: 0.5}})
    assert mb.check_spend(limits, tokens_in=1, tokens_out=1, cost_usd=0.49) is None
    breach = mb.check_spend(limits, tokens_in=1, tokens_out=1, cost_usd=0.51)
    assert breach is not None and breach.code == "COST_BUDGET_EXCEEDED"


def test_a_disabled_ceiling_never_trips():
    limits = mb.Limits.for_task({"meta": {mb.TOKENS_KEY: 0, mb.COST_KEY: 0}})
    assert mb.check_spend(limits, tokens_in=10 ** 9, tokens_out=10 ** 9, cost_usd=10 ** 6) is None


# ------------------------------------------------------------ stalled context

def _answers(*texts):
    return [{"role": "assistant", "content": t} for t in texts]


def test_repeated_identical_answers_are_a_stall():
    limits = mb.Limits.for_task({"meta": {mb.STALLED_KEY: 2}})
    assert mb.check_stalled_context(limits, _answers("x", "y")) is None
    assert mb.check_stalled_context(limits, _answers("x", "x")) is None      # not yet N+1
    breach = mb.check_stalled_context(limits, _answers("x", "x", "x"))
    assert breach is not None and breach.code == "STALLED_CONTEXT"


def test_progress_is_never_a_stall():
    """The negative control that matters most: a run that keeps saying new
    things must never be stopped, however long it runs."""
    limits = mb.Limits.for_task({"meta": {mb.STALLED_KEY: 2}})
    assert mb.check_stalled_context(limits, _answers(*[f"step {i}" for i in range(50)])) is None
    mixed = _answers("same", "same", "different", "same", "same")
    assert mb.check_stalled_context(limits, mixed) is None


def test_tool_call_messages_are_not_counted_as_stalled_prose():
    """A model emitting tool calls is working; identical *prose* is the signal,
    and the tool loop is the other guard's job."""
    limits = mb.Limits.for_task({"meta": {mb.STALLED_KEY: 1}})
    msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]} for _ in range(9)]
    assert mb.check_stalled_context(limits, msgs) is None


# ------------------------------------------------------------ identical calls

_CALL_SEQ = iter(range(1, 10_000))


async def _tool_row(env, run_id, task_id, *, tool="terminal.run", h="H", status="executed"):
    """A distinct `call_id` per row: (run_id, call_id) is unique, and the guard
    counts repeated ARGUMENTS, not repeated provider call ids."""
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(tool_calls_t).values(
            run_id=run_id, task_id=task_id, step=0, call_id=f"c{next(_CALL_SEQ)}", tool=tool,
            args={}, args_hash=h, effect="auto", status=status))
        await s.commit()


async def test_identical_calls_trip_the_loop_guard(env):
    stack = await make_stack(env.client)
    tid, run = stack["task"]["id"], await env.svc.engine.claim()
    limits = mb.Limits.for_task({"meta": {mb.IDENTICAL_KEY: 3}})
    for _ in range(3):
        await _tool_row(env, run, tid)
    assert await mb.check_identical_calls(env.svc, limits, run) is None
    await _tool_row(env, run, tid)
    breach = await mb.check_identical_calls(env.svc, limits, run)
    assert breach is not None and breach.code == "IDENTICAL_CALL_LOOP"


async def test_distinct_calls_are_never_a_loop(env):
    stack = await make_stack(env.client)
    tid, run = stack["task"]["id"], await env.svc.engine.claim()
    limits = mb.Limits.for_task({"meta": {mb.IDENTICAL_KEY: 2}})
    for i in range(20):
        await _tool_row(env, run, tid, h=f"H{i}")
    assert await mb.check_identical_calls(env.svc, limits, run) is None


async def test_denied_and_rejected_calls_are_not_a_loop(env):
    """The gate refusing the same call repeatedly is the gate WORKING. Failing
    the run for that would punish the safe outcome."""
    stack = await make_stack(env.client)
    tid, run = stack["task"]["id"], await env.svc.engine.claim()
    limits = mb.Limits.for_task({"meta": {mb.IDENTICAL_KEY: 2}})
    for status in ("denied", "rejected", "denied", "rejected", "denied", "error"):
        await _tool_row(env, run, tid, status=status)
    assert await mb.check_identical_calls(env.svc, limits, run) is None


# ------------------------------------------------------------------ engine

async def test_a_run_over_its_token_budget_fails_with_a_named_reason(env):
    """End to end: the pathology stops itself instead of waiting for a human."""
    stack = await make_stack(env.client, max_steps=20)
    tid = stack["task"]["id"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(
            meta={mb.TOKENS_KEY: 50}))
        await s.commit()
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("текст", tokens=(400, 100))

    run = await env.svc.engine.claim()
    await env.svc.engine.execute(run)

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.status, runs_t.c.error).where(
            runs_t.c.id == run))).first()
    assert row._mapping["status"] == "failed"
    assert "TOKEN_BUDGET_EXCEEDED" in (row._mapping["error"] or "")
    task = (await env.client.get(f"/api/tasks/{tid}")).json()["task"]
    assert task["status"] == "failed"            # never "completed" on a budget stop


async def test_a_normal_run_is_untouched_by_the_guards(env):
    """The negative control for the whole feature: default limits must not
    change the behaviour of an ordinary task."""
    stack = await make_stack(env.client)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    run = await env.svc.engine.claim()
    await env.svc.engine.execute(run)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "completed"


async def test_a_tool_loop_stops_without_burning_the_whole_budget(env):
    """The guard the token ceiling cannot replace: a loop is caught after a
    handful of calls instead of after a million tokens have been paid for."""
    calls: list = []
    _install("test.echo", calls=calls)
    adapter = ToolAdapter([("tool", "test_echo", {"text": "one"})] * 30 + [("text", "готово")])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter, max_steps=30)
    tid = stack["task"]["id"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(
            meta={mb.IDENTICAL_KEY: 4}))
        await s.commit()

    assert await _run_task(env, tid, timeout=20, until=FINISHED) == "failed"
    async with env.svc.db.session() as s:
        error = (await s.execute(sa.select(runs_t.c.error).where(
            runs_t.c.task_id == tid))).scalar()
    assert "IDENTICAL_CALL_LOOP" in (error or "")
    assert len(calls) <= 8, f"цикл прокрутился {len(calls)} раз до остановки"


# ------------------------------------------------------------------ metrics

async def test_metrics_report_what_actually_happened(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово", tokens=(120, 30))
    run = await env.svc.engine.claim()
    await env.svc.engine.execute(run)
    await env.svc.approvals.create(kind="tool", preview="p", task_id=tid, run_id=run)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(interventions_t).values(
            target_kind="task", target_id=tid, reason="owner stopped", action="stopped"))
        await s.commit()

    m = (await env.client.get(f"/api/tasks/{tid}/efficiency")).json()
    assert m["tokens_total"] == 150
    assert m["approvals_per_successful_mission"] == 1
    assert m["interventions_per_mission"] == 1
    assert m["review_cycles"] == 0 and m["replans"] == 0


async def test_tokens_per_verified_effect_is_none_when_nothing_was_verified(env):
    """"We cannot say" beats a fabricated ratio: dividing by attempted effects
    would make a run look more efficient the more of them failed."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    m = await mb.run_metrics(env.svc, tid)
    assert m["tokens_per_verified_effect"] is None
