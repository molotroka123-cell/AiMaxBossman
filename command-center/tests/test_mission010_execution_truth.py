"""Fresh Windows-safe reproductions of mission 010 completion defects."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import approvals, missions, tasks, tool_calls, utcnow
from bcc.finalize import finalize_override, finalize_task
from bcc.features import missions as mission_feature
from bcc.tools import REGISTRY

from .helpers import make_stack
from .test_finalize_gate import _allow_root, _set_meta, _status, _run_once


async def _call(env, run_id, task_id, *, tool="terminal.run", command="python mutate.py",
                status="executed", preview="exit_code=0", cid="one"):
    spec = REGISTRY.get(tool)
    await env.svc.engine._record_tool_call(
        run_id, task_id, 0, SimpleNamespace(id=cid, name=tool, arguments={"command": command}),
        spec, effect="auto", status=status, preview=preview)


@pytest.mark.parametrize("status,preview", [("error", "cwd outside allowed roots"),
                                            ("denied", "policy refusal"),
                                            ("executed", "exit_code=1\nSyntaxError"),
                                            ("executed", "still running")])
async def test_failed_mutation_never_finalizes_even_if_intent_classifier_missed(env, status, preview):
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _call(env, run, tid, status=status, preview=preview)
    decision = await finalize_task(env.svc.engine, run, tid, answer="I could not do it", usage={})
    assert not decision.ok
    assert await _status(env, tid) != "completed"


async def test_successful_mutation_without_postcondition_is_not_proof(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _call(env, run, tid)
    result = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert not result.ok and "post-state" in result.reason


async def test_optional_failed_read_probe_does_not_veto_an_answer(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _call(env, run, tid, command="git status", status="error")
    assert (await finalize_task(env.svc.engine, run, tid, answer="Available evidence", usage={})).ok


async def test_real_tool_denial_and_honest_model_failure_cannot_complete(env, tmp_path):
    from .test_v21_tool_loop import ToolAdapter, _stack_with_tools
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    await _allow_root(env, allowed)
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "python mutate.py", "cwd": str(forbidden)}),
                           ("text", "The operation failed; I could not perform it.")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Please perform the requested operation")
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"permissions": {"terminal.run": True}})
    await _run_once(env)
    tid = stack["task"]["id"]
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(tool_calls).where(tool_calls.c.task_id == tid))).mappings().all()
    assert rows and rows[0]["status"] in ("error", "denied")
    assert await _status(env, tid) != "completed"
    assert not (forbidden / "mutate.py").exists()


async def test_unrelated_capability_evidence_cannot_verify_mutation(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _call(env, run, tid)
    await _set_meta(env, tid, {"required_effects": [{"kind": "process", "target": "1",
                                                    "expect": {"running": True}}]})
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert not decision.ok and "matching post-state" in decision.reason


async def test_recovered_mutation_with_fresh_readback_completes(env, tmp_path):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    target = tmp_path / "proof.txt"
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(target),
                                                    "expect": {"contains": "verified"}}]})
    await _call(env, run, tid, status="error")
    target.write_text("verified", encoding="utf-8")
    await _call(env, run, tid, cid="retry")
    assert (await finalize_task(env.svc.engine, run, tid, answer="done", usage={})).ok


async def test_malformed_required_effect_cannot_disappear(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _set_meta(env, tid, {"required_effects": [{"kind": "imaginary", "target": "proof"}]})
    assert not (await finalize_task(env.svc.engine, run, tid, answer="done", usage={})).ok


async def test_human_review_cannot_waive_absent_required_effect(env, tmp_path):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(tmp_path / "absent.txt"),
                                                    "expect": {"exists": True}}]})
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks).where(tasks.c.id == tid).values(status="waiting_approval"))
        aid = (await s.execute(sa.insert(approvals).values(task_id=tid, run_id=run,
                     kind="review_escalation", status="approved", decided_by="owner"))).inserted_primary_key[0]
        await s.commit()
    assert not await finalize_override(env.svc, tid, approval={"id": aid, "run_id": run})
    assert await _status(env, tid) == "waiting_approval"


@pytest.mark.parametrize("child_status", ["failed", "stopped"])
async def test_required_child_failure_never_completes_parent(env, child_status):
    async with env.svc.db.session() as s:
        mid = (await s.execute(sa.insert(missions).values(title="parent", goal="proof", status="running",
                                   started_at=utcnow()))).inserted_primary_key[0]
        for status in ["completed", child_status]:
            await s.execute(sa.insert(tasks).values(title="child", prompt="proof", mission_id=mid, status=status))
        await s.commit()
    await mission_feature._tick(env.svc)
    assert (await mission_feature._mission(env.svc, mid))["status"] == "failed"


async def test_all_successful_children_complete_parent(env):
    async with env.svc.db.session() as s:
        mid = (await s.execute(sa.insert(missions).values(title="parent", goal="proof", status="running",
                                   started_at=utcnow()))).inserted_primary_key[0]
        await s.execute(sa.insert(tasks).values(title="child", prompt="proof", mission_id=mid, status="completed"))
        await s.commit()
    await mission_feature._tick(env.svc)
    assert (await mission_feature._mission(env.svc, mid))["status"] == "completed"
