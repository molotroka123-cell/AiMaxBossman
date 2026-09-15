"""§7 — fewer owner questions, identical authority.

The acceptance corpus recorded 161 confirmations in one session, ~121 of them
while correcting a documentation file. Every test here answers one of two
questions:

  * does the mechanism actually remove questions? (the storm cases), and
  * does it grant anything the owner did not explicitly authorize?
    (the negative controls — the larger half of this file on purpose)

A change that reduced the approval count by widening authority would pass the
first kind and fail the second. Both must hold.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc import approval_scope as scope
from bcc.db import (approval_leases as leases_t, approvals as approvals_t,
                    tasks as tasks_t, tool_calls as tool_calls_t, utcnow)

from .helpers import make_stack


# ---------------------------------------------------------------- classifier

@pytest.mark.parametrize("command", [
    "ls -la", "pwd", "cat notes.txt", "git status", "grep -rn foo .",
    "echo hello", "pytest -q", "find . -name '*.py'",
])
def test_read_commands_classify_as_read(command):
    assert scope.effect_class("terminal.run", {"command": command}) == scope.READ


@pytest.mark.parametrize("command", [
    "rm -rf /tmp/x",                      # obvious
    "echo x > notes.txt",                 # write only via redirect
    "echo x >> notes.txt",
    "git commit -m x",                    # write-capable git subcommand
    "git config user.name bob",
    "python fix.py",                      # unknown head → write, the safe way
    "python -m pytest",                   # same: `python` is not a known reader
    "ruff format .",
    "curl -o out.bin https://example.com",
])
def test_write_commands_never_classify_as_read(command):
    """The exact negative control that keeps a read lease from covering a write.
    A single miss here turns "one approval for reading" into arbitrary mutation."""
    assert scope.effect_class("terminal.run", {"command": command}) == scope.WRITE


def test_a_redacted_command_is_never_read():
    """The audit copy of the arguments is scrubbed. An unclassifiable command
    must fall to the strict side, not the convenient one."""
    assert scope.effect_class("terminal.run", {"command": "***REDACTED***"}) == scope.WRITE
    assert scope.effect_class("terminal.run", {"command": ""}) == scope.WRITE


def test_an_unknown_tool_is_never_read():
    assert scope.effect_class("some.unregistered.tool", {}) == scope.WRITE


# ---------------------------------------------------------------- lease core

async def _real_ids(env, cache={}):
    """Leases carry real foreign keys, so the tests need a real task and agent
    rather than invented ids."""
    key = id(env)
    if key not in cache:
        cache.clear()
        stack = await make_stack(env.client)
        second = (await env.client.post("/api/tasks", json={
            "title": "second", "prompt": "p", "agent_id": stack["agent"]["id"]})).json()["task"]
        cache[key] = (stack["task"]["id"], second["id"], stack["agent"]["id"])
    return cache[key]


async def _lease(env, *, effect=scope.READ, tool="terminal.run", key="sandbox",
                 uses=10, ttl=900, task_slot=0, agent_id=None):
    first, second, agent = await _real_ids(env)
    task_id = (first, second)[task_slot]
    sc = scope.Scope(tool=tool, effect_class=effect, scope_key=key,
                     agent_id=agent_id if agent_id is not None else agent, task_id=task_id)
    return sc, await scope.grant(env.svc, approval={"id": None}, scope=sc,
                                 max_uses=uses, ttl_seconds=ttl)


async def test_a_lease_covers_repeated_in_scope_calls(env):
    sc, lease = await _lease(env, uses=3)
    assert lease["max_uses"] == 3
    for expected in (1, 2, 3):
        used = await scope.consume(env.svc, sc)
        assert used is not None and used["used"] == expected
    assert await scope.consume(env.svc, sc) is None      # bounded, not endless


async def test_a_read_lease_does_not_cover_a_write(env):
    sc, _ = await _lease(env, effect=scope.READ)
    write = scope.Scope(tool=sc.tool, effect_class=scope.WRITE, scope_key=sc.scope_key,
                        agent_id=sc.agent_id, task_id=sc.task_id)
    assert await scope.consume(env.svc, write) is None


@pytest.mark.parametrize("field,value", [
    ("tool", "browser.open"), ("scope_key", "project_host"),
    ("agent_id", 999), ("task_id", 999),
])
async def test_a_lease_covers_only_its_own_scope(env, field, value):
    """Every dimension of the scope is load-bearing: an owner who authorized
    sandbox reads for agent 1 on task 1 authorized nothing else."""
    sc, _ = await _lease(env)
    fields = {"tool": sc.tool, "effect_class": sc.effect_class, "scope_key": sc.scope_key,
              "agent_id": sc.agent_id, "task_id": sc.task_id}
    other = scope.Scope(**{**fields, field: value})
    assert await scope.consume(env.svc, other) is None


async def test_an_expired_lease_is_not_spendable(env):
    sc, lease = await _lease(env, ttl=60)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(leases_t).where(leases_t.c.id == lease["id"]).values(
            expires_at=utcnow() - timedelta(seconds=1)))
        await s.commit()
    assert await scope.consume(env.svc, sc) is None


async def test_revocation_is_immediate(env):
    sc, lease = await _lease(env, uses=10)
    assert await scope.consume(env.svc, sc) is not None
    await scope.revoke(env.svc, int(lease["id"]))
    assert await scope.consume(env.svc, sc) is None


async def test_revoking_a_task_kills_every_lease_it_granted(env):
    first, second, _agent = await _real_ids(env)
    sc_a, _ = await _lease(env, task_slot=0, effect=scope.READ)
    sc_b, _ = await _lease(env, task_slot=0, effect=scope.WRITE)
    sc_other, _ = await _lease(env, task_slot=1)
    assert await scope.revoke_for_task(env.svc, first) == 2
    assert await scope.consume(env.svc, sc_a) is None
    assert await scope.consume(env.svc, sc_b) is None
    assert await scope.consume(env.svc, sc_other) is not None   # other missions untouched


async def test_bounds_are_clamped_not_trusted(env):
    """A caller cannot mint an eternal or unlimited authority, even by asking."""
    first, _second, agent = await _real_ids(env)
    sc = scope.Scope(tool="terminal.run", effect_class=scope.READ, scope_key="",
                     agent_id=agent, task_id=first)
    lease = await scope.grant(env.svc, approval={"id": None}, scope=sc,
                              max_uses=10 ** 9, ttl_seconds=10 ** 9)
    assert lease["max_uses"] == scope.MAX_LEASE_USES
    assert (lease["expires_at"] - utcnow()).total_seconds() <= scope.MAX_LEASE_TTL_SECONDS + 5
    zero = await scope.grant(env.svc, approval={"id": None}, scope=sc,
                             max_uses=0, ttl_seconds=0)
    assert zero["max_uses"] >= 1                                # no zero-use ghost lease


# ---------------------------------------------------------------- API surface

async def test_deciding_without_a_lease_field_grants_no_lease(env):
    """The default path must be byte-for-byte the old behaviour."""
    appr = await env.svc.approvals.create(kind="tool", preview="p", task_id=None, run_id=None)
    res = await env.client.post(f"/api/approvals/{appr['id']}", json={"approve": True})
    assert res.status_code == 200 and res.json().get("lease") is None
    assert await scope.listing(env.svc) == []


async def test_a_lease_cannot_be_minted_without_a_parked_call(env):
    """The scope comes from what the owner was shown, never from the request
    body: an approval with no pending tool call has no scope to grant."""
    appr = await env.svc.approvals.create(kind="tool", preview="p")
    res = await env.client.post(f"/api/approvals/{appr['id']}",
                                json={"approve": True, "lease": {"max_uses": 50, "ttl_seconds": 900}})
    assert res.status_code == 409
    assert await scope.listing(env.svc) == []


async def test_rejecting_with_a_lease_field_still_grants_nothing(env):
    appr = await env.svc.approvals.create(kind="tool", preview="p")
    res = await env.client.post(f"/api/approvals/{appr['id']}",
                                json={"approve": False, "lease": {"max_uses": 9, "ttl_seconds": 60}})
    assert res.status_code == 200
    assert await scope.listing(env.svc) == []


async def test_owner_can_list_and_revoke_leases_over_the_api(env):
    first, _second, _agent = await _real_ids(env)
    _, lease = await _lease(env, task_slot=0)
    listed = (await env.client.get(f"/api/approvals/leases?task_id={first}")).json()["leases"]
    assert [l["id"] for l in listed] == [lease["id"]]
    res = await env.client.post(f"/api/approvals/leases/{lease['id']}/revoke", json={"by": "owner"})
    assert res.status_code == 200 and res.json()["status"] == "revoked"
    assert (await env.client.get(f"/api/approvals/leases?task_id={first}")).json()["leases"] == []


# ---------------------------------------------------------------- storm cases

async def test_the_same_unanswered_question_is_not_asked_twice(env):
    """The queue filling with copies of one unanswered question is what made
    the storm unreadable. Dedup reuses the pending row and grants nothing."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    appr = await env.svc.approvals.create(kind="tool", preview="p", task_id=tid, run_id=run)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(tool_calls_t).values(
            run_id=run, task_id=tid, step=0, call_id="c1", tool="terminal.run",
            args={"command": "ls"}, args_hash="H", effect="ask",
            status="pending_approval", approval_id=appr["id"]))
        await s.commit()
    found = await scope.find_reusable(env.svc, args_hash="H", run_id=run)
    assert found is not None and found["id"] == appr["id"]
    assert found["status"] == "pending"                        # still unanswered
    assert await scope.find_reusable(env.svc, args_hash="OTHER", run_id=run) is None


async def test_a_refusal_is_not_re_asked(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    appr = await env.svc.approvals.create(kind="tool", preview="p", task_id=tid, run_id=run)
    await env.svc.approvals.decide(appr["id"], False, "owner")
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(tool_calls_t).values(
            run_id=run, task_id=tid, step=0, call_id="c1", tool="terminal.run",
            args={"command": "rm -rf /"}, args_hash="H", effect="ask",
            status="rejected", approval_id=appr["id"]))
        await s.commit()
    assert await scope.previously_rejected(env.svc, args_hash="H", run_id=run) is True
    assert await scope.previously_rejected(env.svc, args_hash="H", run_id=run + 999) is False


# ---------------------------------------------------------------- budget

async def test_budget_stops_the_task_it_never_auto_approves(env):
    """An exhausted budget must be a brake, not an accelerator. If exceeding it
    granted the next request, the 'limit' would be an authority expansion."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(
            meta={"approval_budget": 2}))
        await s.commit()
    task = {"id": tid, "meta": {"approval_budget": 2}}
    over, used, budget = await scope.budget_exceeded(env.svc, task)
    assert (over, used, budget) == (False, 0, 2)
    for _ in range(2):
        await env.svc.approvals.create(kind="tool", preview="p", task_id=tid)
    over, used, budget = await scope.budget_exceeded(env.svc, task)
    assert over and used == 2 and budget == 2


async def test_budget_is_per_task_and_configurable(env):
    assert scope.budget_of({"meta": {}}) == scope.DEFAULT_APPROVAL_BUDGET
    assert scope.budget_of({"meta": {"approval_budget": 3}}) == 3
    assert scope.budget_of({"meta": {"approval_budget": "nonsense"}}) == scope.DEFAULT_APPROVAL_BUDGET
    assert scope.budget_of({"meta": {"approval_budget": -5}}) == scope.DEFAULT_APPROVAL_BUDGET
    assert scope.budget_of(None) == scope.DEFAULT_APPROVAL_BUDGET


async def test_only_tool_approvals_count_against_the_tool_budget(env):
    """A review escalation is a different conversation; charging it to the tool
    budget would let a stuck review starve legitimate tool questions."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    await env.svc.approvals.create(kind="review_escalation", preview="p", task_id=tid)
    assert await scope.spent(env.svc, tid) == 0


# ---------------------------------------------------------------- end to end
# The headline acceptance number: a run of many identical-authority read calls
# must cost the owner ONE decision, not one per call.

async def test_ten_read_calls_cost_the_owner_one_approval(env):
    """Reproduces the storm shape from the corpus in miniature: a worker that
    keeps probing the filesystem. Before the lease, each `ls`/`pwd`/`cat` was a
    separate question — T3 alone spent 60 of them. With one scoped decision the
    owner is asked once and the remaining nine calls consume that authority."""
    from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools, _install

    commands = ["pwd", "ls -la", "cat a.txt", "ls /tmp", "git status", "grep -rn x .",
                "find . -name '*.py'", "wc -l a.txt", "head a.txt", "tail a.txt"]
    _install("terminal.run", permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter([("tool", "terminal_run", {"command": c, "mode": "sandbox"})
                           for c in commands] + [("text", "готово")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    max_steps=len(commands) + 2)
    tid = stack["task"]["id"]

    status = await _run_task(env, tid, timeout=15)
    assert status == "waiting_approval"                    # first call asks, as before

    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1
    decided = await env.client.post(f"/api/approvals/{appr[0]['id']}", json={
        "approve": True, "by": "owner",
        "lease": {"max_uses": 20, "ttl_seconds": 600}})
    assert decided.status_code == 200
    lease = decided.json()["lease"]
    assert lease is not None and lease["effect_class"] == scope.READ

    await _run_task(env, tid, timeout=25, until=FINISHED)

    asked = (await env.client.get("/api/approvals?status=")).json()
    tool_asks = [a for a in asked if a["kind"] == "tool"]
    assert len(tool_asks) == 1, f"владельца спросили {len(tool_asks)} раз вместо одного"
    metrics = (await env.client.get(f"/api/approvals/metrics?task_id={tid}")).json()
    assert metrics["approvals_asked"] == 1
    assert metrics["lease_uses"] >= 5                      # the rest rode the lease


async def test_a_write_in_the_middle_of_a_read_lease_still_asks(env):
    """The security half of the same story. A read lease must not carry a
    mutation through silently: the owner is asked again for the write."""
    from .test_v21_tool_loop import ToolAdapter, _run_task, _stack_with_tools, _install

    _install("terminal.run", permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "ls -la", "mode": "sandbox"}),
                           ("tool", "terminal_run", {"command": "rm -rf data", "mode": "sandbox"}),
                           ("text", "готово")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter, max_steps=6)
    tid = stack["task"]["id"]

    assert await _run_task(env, tid, timeout=15) == "waiting_approval"
    first = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{first['id']}", json={
        "approve": True, "by": "owner", "lease": {"max_uses": 50, "ttl_seconds": 600}})

    # Ждём именно НОВОГО вопроса: сразу после решения задача ещё числится
    # waiting_approval, пока воркер её не подхватил — опрос статуса здесь врёт.
    from .conftest import wait_for

    async def new_ask():
        rows = (await env.client.get("/api/approvals")).json()
        return rows or None

    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    env.svc.engine.poll_interval = 0.02
    try:
        pending = await wait_for(new_ask, timeout=20)
    finally:
        worker.cancel(); watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
    assert len(pending) == 1 and "rm -rf" in pending[0]["preview"]
