"""Execution receipts backstop a missed action classifier, without approval loops."""
import pytest
import sqlalchemy as sa

from bcc.db import approvals, task_runs, tasks, tool_calls
from bcc.features import action_contract
from bcc.finalize import _effect_problem

from .test_finalize_gate import _allow_root, _run_once, _status
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools


@pytest.mark.parametrize("answer", ["Done, the operation succeeded.",
                                   "The operation failed; I could not perform it."])
async def test_unclassified_real_denial_fails_without_impossible_approval(env, tmp_path, answer):
    # Deliberately outside the classifier vocabulary. The real terminal
    # executor refuses cwd; the model then supplies either a lie or a refusal.
    prompt = "Please perform the requested operation"
    assert not action_contract.classify_all(prompt)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    await _allow_root(env, allowed)
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "python mutate.py", "cwd": str(forbidden)}),
        ("text", answer),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter, prompt=prompt)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})
    tid = stack["task"]["id"]
    await _run_once(env)
    async with env.svc.db.session() as s:
        task = (await s.execute(sa.select(tasks).where(tasks.c.id == tid))).mappings().one()
        run = (await s.execute(sa.select(task_runs).where(task_runs.c.task_id == tid))).mappings().one()
        rows = (await s.execute(sa.select(tool_calls).where(tool_calls.c.task_id == tid))).mappings().all()
        pending = (await s.execute(sa.select(approvals).where(approvals.c.task_id == tid))).mappings().all()
    assert not (task["meta"] or {}).get("required_effects")
    assert not ((task["meta"] or {}).get("review") or {}).get("evidence")
    assert rows and rows[0]["status"] in ("error", "denied")
    assert await _status(env, tid) == "failed"
    assert run["status"] == "failed" and run["result"] == answer
    assert "effectful" in run["error"]
    assert not pending  # no impossible review_escalation for a denied effect
    assert not (forbidden / "mutate.py").exists()
    assert not any(e["kind"] in ("task.completed", "task.finalized")
                   for e in await env.svc.bus.recent(100))


@pytest.mark.parametrize("status,preview", [("error", "failure"), ("denied", "policy"),
    ("rejected", "owner refused"), ("executed", "exit_code=1"),
    ("executed", "still running")])
def test_unclassified_outcome_is_checked(status, preview):
    row = {"tool": "terminal.run", "args": {"command": "python mutate.py"},
           "status": status, "result_preview": preview}
    assert _effect_problem([row], [])
    # Same exact action can recover; an unrelated successful probe cannot.
    successful = {**row, "status": "executed", "result_preview": "exit_code=0"}
    assert not _effect_problem([row, successful], [])
    assert _effect_problem([row, {**successful, "args": {"command": "git status"}}], [])


def test_successful_opaque_capability_does_not_get_an_impossible_verifier():
    assert not _effect_problem([{"tool": "mcp.opaque", "status": "executed",
                                 "args": {"value": 1}}], [])


@pytest.mark.parametrize("command", ["find . -delete", "find . -exec rm {} +",
                                    "find . -fprint output", "find . -fprint0 output",
                                    "find . -fprintf output %p", "find . -fls output",
                                    "rg foo --pre cat", "rg foo --pre=cat",
                                    "ruff check --fix .", "ruff format .",
                                    "ruff --config ruff.toml format .", "black .", "isort ."])
def test_write_modes_of_diagnostic_commands_cannot_hide_failure(command):
    assert _effect_problem([{"tool": "terminal.run", "args": {"command": command},
                             "status": "executed", "result_preview": "exit_code=1"}], [])


def test_failed_read_probe_does_not_invalidate_successful_mutation():
    mutation = {"tool": "terminal.run", "args": {"command": "python mutate.py"},
                "status": "executed", "result_preview": "exit_code=0"}
    probe = {"tool": "terminal.run", "args": {"command": "git status"},
             "status": "error", "error": "diagnostic failed"}
    assert not _effect_problem([mutation, probe], [])
    assert _effect_problem([mutation, {**mutation, "error": "receipt failure"}], [])


async def test_approved_real_nonzero_process_cannot_complete_unclassified_task(env, tmp_path):
    await _allow_root(env, tmp_path)
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": 'python -c "raise SystemExit(1)"',
                                  "mode": "project_host", "cwd": str(tmp_path)}),
        ("text", "Done, successfully completed."),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                    prompt="Please perform the requested operation", max_steps=6)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})
    tid = stack["task"]["id"]
    assert await _run_task(env, tid) == "waiting_approval"
    approval = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{approval['id']}", json={"approve": True, "by": "test"})
    assert await _run_task(env, tid, until=FINISHED) == "failed"
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls).where(tool_calls.c.task_id == tid))).mappings().one()
        reviews = (await s.execute(sa.select(approvals).where(
            approvals.c.task_id == tid, approvals.c.kind == "review_escalation"))).all()
    assert row["status"] == "executed" and row["result_preview"].startswith("exit_code=1")
    assert not reviews
