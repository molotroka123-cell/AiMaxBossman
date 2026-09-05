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
