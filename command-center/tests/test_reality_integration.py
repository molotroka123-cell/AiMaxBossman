"""Reality at actual BCC SQLite engine boundaries; local file IO, no provider."""
from dataclasses import asdict
from pathlib import Path
import sys

import pytest
import sqlalchemy as sa

from bossman_shared import reality_guard as guard
from bcc.db import task_runs as runs_t
from bcc.providers import ToolCall
from bcc.tools import ToolResult, ToolSpec
from .helpers import make_stack

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "reality"))
from test_host import fixture_host


@pytest.fixture
def protection(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "STATE_ROOT", tmp_path / "protected")
    monkeypatch.setattr(guard, "_hosts", {})
    monkeypatch.setenv("BOSSMAN_REALITY_ENABLED", "1")


async def prepared(env, tmp_path):
    stack = await make_stack(env.client)
    run_id = await env.svc.engine.claim()
    host, mission, args, target = fixture_host(tmp_path, executor=str(stack["agent"]["id"]), run=str(run_id))
    guard.install("test", host)
    guard.enroll("bcc", stack["task"]["id"], run_id, asdict(mission),
                 trusted_ir=asdict(mission), profile="test")
    return stack, run_id, host, args, target


async def set_final_answer(env, run_id):
    # A persisted model answer is deliberately injected; no provider call.
    async with env.svc.db.session() as session:
        await session.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
            checkpoint={"messages": [{"role": "assistant", "content": "done"}], "step": 1}))
        await session.commit()


async def test_done_without_receipts_is_parked_and_not_reclaimed(env, tmp_path, protection):
    stack, run_id, _, _, _ = await prepared(env, tmp_path)
    await set_final_answer(env, run_id)
    await env.svc.engine.execute(run_id)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "waiting_approval"
    assert await env.svc.engine.claim() is None


async def test_real_file_dispatch_and_all_completion_gates(env, tmp_path, protection):
    stack, run_id, host, args, target = await prepared(env, tmp_path)
    async def handler(arguments, ctx):
        target.write_text(arguments["content"], encoding="utf-8")
        return ToolResult(content="file written")
    spec = ToolSpec(name="file.write", description="controlled local writer", handler=handler,
                    input_schema={}, default_effect="auto")
    await env.svc.engine._run_tool_now(run_id, stack["task"], stack["agent"], [],
        ToolCall(id="write-1", name=spec.name, arguments=args), spec, 1)
    await set_final_answer(env, run_id)
    await env.svc.engine.execute(run_id)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "completed"
    assert target.read_text() == "verified"


@pytest.mark.parametrize("failure", ["off", "missing_profile", "wrong_run", "missing_ir"])
async def test_direct_finalization_cannot_bypass_gate(env, tmp_path, protection, monkeypatch, failure):
    stack, run_id, host, _, _ = await prepared(env, tmp_path)
    if failure == "off": monkeypatch.setenv("BOSSMAN_REALITY_ENABLED", "0")
    if failure == "missing_profile": guard._hosts.clear()
    if failure == "wrong_run": run_id += 1
    if failure == "missing_ir": host.path.unlink()
    with pytest.raises(Exception):
        await env.svc.engine._finish(run_id, stack["task"]["id"], "completed", result="done")
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] != "completed"


async def test_provider_and_fallback_not_called_for_participant(env, tmp_path, protection):
    stack, run_id, _, _, _ = await prepared(env, tmp_path)
    def forbidden(*a, **kw): pytest.fail("provider/fallback egress reached")
    env.svc.registry.adapter_factory = forbidden
    await env.svc.engine.execute(run_id)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "failed"
    assert await env.svc.engine.claim() is None


@pytest.mark.parametrize("revocation", ["tool_grant", "policy_deny"])
async def test_approved_tool_resume_rechecks_current_authorization(env, revocation):
    from bcc.db import agents as agents_t
    from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools
    from .test_secrem_f013_approval_identity import _approve_first
    calls = []
    _install("terminal.run", calls=calls, permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "git push"}), ("text", "ok")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "waiting_approval"
    await _approve_first(env)
    values = {"tools": []} if revocation == "tool_grant" else {
        "permissions": {"tool_rules": [{"tool": "terminal.run", "effect": "deny"}]}}
    async with env.svc.db.session() as session:
        await session.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(**values))
        await session.commit()
    await _run_task(env, stack["task"]["id"], until=FINISHED)
    assert calls == [], "approval must not override a subsequent grant revocation or DENY"


@pytest.mark.parametrize("parking", ["critical_gate", "tool_approval"])
async def test_stale_worker_cannot_park_new_owner_completed_task(env, parking):
    from bcc.engine import CriticalHookFailure, FencedOut
    from .test_fence_fl01 import _takeover, _run_row
    stack = await make_stack(env.client)
    old = env.svc.engine
    run_id = await old.claim()
    new = await _takeover(env, old, run_id)
    task_id = stack["task"]["id"]
    await new._finish(run_id, task_id, "completed", result="new owner verified result")
    with pytest.raises(FencedOut):
        if parking == "critical_gate":
            await old._escalate_gate_failure(run_id, stack["task"], [], 1,
                CriticalHookFailure("gate_completion", "test_gate", "late old-worker failure"))
        else:
            await old._park_for_approval(run_id, task_id, [], 1, {"tool": "terminal.run"}, {})
    row = await _run_row(env.svc.db, run_id)
    task = (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]
    assert row["status"] == task["status"] == "completed"
    assert row["result"] == "new owner verified result"


@pytest.mark.parametrize("checkpoint_kind", ["new", "old_system", "no_system", "already_injected"])
async def test_actual_bcc_model_payload_receives_protocol_on_new_and_resumed_runs(env, checkpoint_kind):
    from bossman_shared.reasoning_protocol import reasoning_protocol_prompt, with_reasoning_protocol
    from .test_v21_tool_loop import ToolAdapter
    adapter = ToolAdapter([("text", "4")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client, max_steps=3, prompt="Original authorized user task")
    run_id = await env.svc.engine.claim()
    if checkpoint_kind != "new":
        messages = [{"role": "user", "content": "Original authorized user task"}]
        if checkpoint_kind != "no_system":
            system = "Keep old security policy"
            if checkpoint_kind == "already_injected":
                system = with_reasoning_protocol(system)
            messages.insert(0, {"role": "system", "content": system})
        async with env.svc.db.session() as session:
            await session.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                checkpoint={"messages": messages, "step": 1}))
            await session.commit()
    await env.svc.engine.execute(run_id)
    assert adapter.calls == 1
    messages = adapter.seen_messages[0]
    systems = [m["content"] for m in messages if m["role"] == "system"]
    assert sum(s.count(reasoning_protocol_prompt()) for s in systems) == 1
    expected = "Keep old security policy" if checkpoint_kind in ("old_system", "already_injected") else stack["agent"]["system_prompt"]
    assert systems[0].startswith(expected)
    assert any(m["role"] == "user" and m["content"] == "Original authorized user task" for m in messages)


@pytest.mark.parametrize("prompt_kind", ["terminal_action", "live_task7"])
async def test_failed_terminal_effect_and_honest_model_failure_never_complete(env, tmp_path, prompt_kind):
    from bcc.db import tool_calls as tool_calls_t
    from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools
    calls = []
    async def failed_process(arguments, ctx):
        calls.append(arguments)
        return ToolResult(content="process exited with code 17; no effect verified", error=True)
    _install("terminal.run", handler=failed_process, source="terminal", category="exec", default_effect="auto")
    command = 'python -c "from pathlib import Path; Path(\'glm_acceptance.txt\').write_text(\'GLM_OK\', encoding=\'utf-8\')"'
    args = {"command": command, "mode": "project_host", "cwd": tmp_path.as_posix()}
    prompt = ("Safe file mutation acceptance: use terminal.run in project_host mode with cwd "
              + tmp_path.as_posix() + " and exactly this command: " + command
              + ". Then report the observed tool result honestly.") if prompt_kind == "live_task7" else "Выполни команду в терминале"
    adapter = ToolAdapter([("tool", "terminal_run", args),
                           ("text", "Не удалось выполнить команду: процесс завершился с ошибкой.")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter,
                                   prompt=prompt)
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) == "failed"
    assert calls == [args]
    assert not (tmp_path / "glm_acceptance.txt").exists()
    async with env.svc.db.session() as session:
        rows = (await session.execute(sa.select(tool_calls_t.c.status).where(
            tool_calls_t.c.task_id == stack["task"]["id"]))).scalars().all()
    assert rows == ["error"]
