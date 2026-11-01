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
async def test_failed_mutation_never_finalizes_an_owed_effect(env, tmp_path, status, preview):
    """A mutation that was refused, errored, exited non-zero or never finished
    cannot finalize a task that OWES the effect.

    The obligation is declared, which is what makes it enforceable. The
    finalizer deliberately does not manufacture obligations out of prompt text
    or out of the bare presence of a tool row: for the families with no wired
    post-state verifier (apps, openclaw, opencode, plugin, mcp) `verify_all`
    can never answer VERIFIED, so such a manufactured obligation is not a gate
    but a permanent dead end — the task parks in `waiting_approval` behind a
    `review_escalation` that `finalize_override` re-refuses forever. The
    zero-attempt and failed-attempt cases of a CLASSIFIED action task are held
    one layer up, by `features/action_contract._gate`, which counts only
    `executed` calls of the matching non-reading family (see
    tests/test_action_contract.py::test_family_matrix_rejected_or_errored_call_is_not_evidence).
    """
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(tmp_path / "owed.txt"),
                                                    "expect": {"exists": True}}]})
    await _call(env, run, tid, status=status, preview=preview)
    decision = await finalize_task(env.svc.engine, run, tid, answer="I could not do it", usage={})
    assert not decision.ok
    assert await _status(env, tid) != "completed"


async def test_successful_mutation_without_postcondition_is_not_proof(env, tmp_path):
    """`exit_code=0` is the executor's claim, never the world's answer: a
    declared post-state that is absent refuses the finalize even though the
    mutation reported success."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(tmp_path / "claimed.txt"),
                                                    "expect": {"exists": True}}]})
    await _call(env, run, tid)
    result = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert not result.ok and "not verified" in result.reason


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
    tid = stack["task"]["id"]
    # The task owes the file the denied command would have written; the denial
    # leaves that obligation unmet, so no honest answer can finalize it.
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(forbidden / "mutate.py"),
                                                    "expect": {"exists": True}}]})
    await _run_once(env)
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


async def test_failed_attempt_then_verified_post_state_finalizes(env, tmp_path):
    """Long-horizon run 30.10: a first script attempt exited 1, the agent fixed it (a NEW successful command) and
    the owed file exists with the right content. The earlier attempt is superseded — the task completes."""
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(tmp_path / "owed.txt"),
                                                    "expect": {"exists": True}}]})
    await _call(env, run, tid, status="executed", preview="exit_code=1\nTraceback")
    await _call(env, run, tid, command="python mutate_fixed.py", cid="fixed")
    import os
    import time
    (tmp_path / "owed.txt").write_text("ok", encoding="utf-8")          # produced by the successful retry
    later = time.time() + 5
    os.utime(tmp_path / "owed.txt", (later, later))
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert decision.ok, decision.reason


async def test_failed_attempt_alone_is_not_superseded_by_its_own_partial_output(env, tmp_path):
    """A crashed script may leave a partial output behind (written after the attempt started). Without a later
    successful call that output is not proof — the failed attempt still blocks completion."""
    import os
    import time
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(tmp_path / "owed.txt"),
                                                    "expect": {"exists": True}}]})
    await _call(env, run, tid, status="executed", preview="exit_code=1\nTraceback")
    (tmp_path / "owed.txt").write_text("partial", encoding="utf-8")
    later = time.time() + 5
    os.utime(tmp_path / "owed.txt", (later, later))
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert not decision.ok and decision.reason == "effectful terminal outcome is failed or still unobserved"
    assert await _status(env, tid) != "completed"


async def test_lh_20261031_relative_outputs_superseded_by_later_success_finalize(env, tmp_path):
    """Live long-horizon run 31.10, task 857 (synthetic): `python3` absent (exit 49), `import pandas` failed
    (exit 1), a csv-module rewrite exited 0 and wrote the outputs. The Auto Full prompt uses RELATIVE paths, so the
    declared evidence is `out/monthly_region.csv`; review verified it, finalize refused 3x -> REVIEW_ESCALATION_EXHAUSTED.
    The relative target is resolved through the verifier roots, the later success supersedes both failures."""
    import os
    import time
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"review": {"evidence": [{"kind": "file", "target": "out/monthly_region.csv",
                                                        "expect": {"exists": True}}]}})
    await _call(env, run, tid, command="mkdir -p out && python3 -c \"import pandas\"", status="executed",
                preview="exit_code=49\nPython was not found", cid="py3")
    await _call(env, run, tid, command="mkdir -p out && python -c \"import pandas\"", status="executed",
                preview="exit_code=1\nModuleNotFoundError: No module named 'pandas'", cid="pandas")
    await _call(env, run, tid, command="mkdir -p out && python -c \"import csv\"", cid="csv")
    (tmp_path / "out").mkdir()
    out = tmp_path / "out" / "monthly_region.csv"
    out.write_text("region,month,revenue\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(out, (later, later))
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert decision.ok, decision.reason
    assert await _status(env, tid) == "completed"


async def _call_in(env, run_id, task_id, cwd, *, command, preview, cid):
    spec = REGISTRY.get("terminal.run")
    await env.svc.engine._record_tool_call(
        run_id, task_id, 0, SimpleNamespace(id=cid, name="terminal.run",
                                            arguments={"command": command, "mode": "project_host", "cwd": str(cwd)}),
        spec, effect="auto", status="executed", preview=preview)


@pytest.mark.parametrize("writes", [True, False])
async def test_lh_20261031_876_input_evidence_finalize(env, tmp_path, writes):
    """LH rerun 31.10, task 876: declared evidence is the INPUT orders.csv (pre-existing, untouched); four analyses
    exited 1, then successes. Completes only when a later success wrote a fresh file in the run folder."""
    import os
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    folder = tmp_path / "lh-run"            # NOT tmp_path itself: the test DB lives there and is written meanwhile
    folder.mkdir()
    await _allow_root(env, folder)
    orders = folder / "orders.csv"
    orders.write_text("order_id\n1\n", encoding="utf-8")
    os.utime(orders, (1_000_000_000, 1_000_000_000))
    await _set_meta(env, tid, {"review": {"evidence": [{"kind": "file", "target": "orders.csv",
                                                        "expect": {"exists": True}}]}})
    for i in range(4):
        await _call_in(env, run, tid, folder, command=f"python -c 'import pandas  # {i}'",
                       preview="exit_code=1\nTraceback", cid=f"f{i}")
    await _call_in(env, run, tid, folder, command="python -c 'print(1)'", preview="exit_code=0\n1", cid="probe")
    await _call_in(env, run, tid, folder, command="python -c 'open(\"notes/03_findings.md\",\"w\")'",
                   preview="exit_code=0\n", cid="write")
    if writes:
        (folder / "notes").mkdir()
        (folder / "notes" / "03_findings.md").write_text("P-07 C-0042", encoding="utf-8")
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    if writes:
        assert decision.ok, decision.reason
    else:
        assert not decision.ok and decision.reason == "effectful terminal outcome is failed or still unobserved"


async def test_relative_output_without_superseding_success_still_refuses(env, tmp_path):
    """Same relative contract, but the only later success wrote a DIFFERENT file: the owed output predates it."""
    import os
    import time
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    await _set_meta(env, tid, {"review": {"evidence": [{"kind": "file", "target": "out/monthly_region.csv",
                                                        "expect": {"exists": True}}]}})
    (tmp_path / "out").mkdir()
    out = tmp_path / "out" / "monthly_region.csv"
    out.write_text("partial", encoding="utf-8")
    await _call(env, run, tid, command="mkdir -p out && python -c \"import pandas\"", status="executed",
                preview="exit_code=1\nTraceback", cid="pandas")
    await _call(env, run, tid, command="python -c \"open('out/other.csv','w')\" > log.txt", cid="other")
    os.utime(out, (1_000_000_000, 1_000_000_000))                          # not rewritten by the success
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert not decision.ok and decision.reason == "effectful terminal outcome is failed or still unobserved"
    assert await _status(env, tid) != "completed"


async def test_failed_attempt_with_a_pre_existing_file_still_refuses(env, tmp_path):
    """Security audit 30.10: a file that was already there (an input, a stale output) must not let a crashed
    attempt complete the task."""
    import os
    stack = await make_stack(env.client, prompt="Please perform the requested operation")
    tid = stack["task"]["id"]
    run = await env.svc.engine.claim()
    await _allow_root(env, tmp_path)
    (tmp_path / "owed.txt").write_text("stale", encoding="utf-8")
    os.utime(tmp_path / "owed.txt", (1_000_000_000, 1_000_000_000))        # long before the attempt
    await _set_meta(env, tid, {"required_effects": [{"kind": "file", "target": str(tmp_path / "owed.txt"),
                                                    "expect": {"exists": True}}]})
    await _call(env, run, tid, status="executed", preview="exit_code=1\nTraceback")
    decision = await finalize_task(env.svc.engine, run, tid, answer="done", usage={})
    assert not decision.ok
