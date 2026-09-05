"""Core real loop/tool/finalization with fixture DB/model; V3 uses real journals."""
from dataclasses import asdict, replace
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from bossman_shared import reality_guard as guard
from bossman_shared.reality.contracts import digest
from bossman.agents import AgentSpec, ToolGrant
from bossman import runner
from bossman.toolkit import ToolDef, ToolResult

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "reality"))
from test_host import fixture_host


@pytest.fixture
def protection(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "STATE_ROOT", tmp_path / "protected")
    monkeypatch.setattr(guard, "_hosts", {})
    monkeypatch.setenv("BOSSMAN_REALITY_ENABLED", "1")


@pytest.mark.parametrize("perform_write", [False, True])
async def test_core_loop_final_text_requires_real_file_receipt(tmp_path, monkeypatch, protection, perform_write):
    host, mission, args, target = fixture_host(tmp_path / "workspace" / "worker", run="1", action="fs.write")
    guard.install("test", host)
    guard.enroll("core", 1, 1, asdict(mission), trusted_ir=asdict(mission), profile="test")
    agent = AgentSpec("worker", "worker", "fixture-local", tools=[ToolGrant("fs.write", False)], max_steps=3)
    agent.path = tmp_path / "agent"
    agent.path.mkdir()
    (agent.path / "prompt.md").write_text(
        "KEEP EXISTING SAFETY POLICY. " + "existing instructions " * 400, encoding="utf-8")
    monkeypatch.setattr(runner, "real_window", lambda _: 4096)
    async def handler(args, ctx):
        target.write_text(args["content"], encoding="utf-8")
        return ToolResult(content="file written")
    tool = ToolDef(name="fs.write", description="controlled writer", rights="write", handler=handler)
    monkeypatch.setattr(runner, "by_api_name", lambda _: tool)
    monkeypatch.setattr(runner, "load_all", lambda: {"worker": agent})
    monkeypatch.setattr(runner.settings, "workspace_dir", tmp_path / "workspace")
    monkeypatch.setattr(runner, "_select_compute", AsyncMock(return_value=(0, [])))
    monkeypatch.setattr(runner._WM, "create_task_state", AsyncMock())
    monkeypatch.setattr(runner._WM, "update_task_state", AsyncMock())
    monkeypatch.setattr(runner.failure_memory, "record_failure", AsyncMock())
    monkeypatch.setattr(runner.db, "fetchrow", AsyncMock(return_value={"id": 1}))
    execute = AsyncMock()
    monkeypatch.setattr(runner.db, "execute", execute)
    # Explicit fixture injection tests the finalization barrier independently of
    # the separate provider-egress barrier, which remains enabled in production.
    monkeypatch.setattr(guard, "block_unmetered_model", lambda *a: None)
    done = {"content": "done", "_usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    response = dict(done, tool_calls=[{"function": {"name": "file_write", "arguments": __import__("json").dumps(args)}}])
    model = AsyncMock(side_effect=[response, done] if perform_write else [done])
    monkeypatch.setattr(runner, "chat", model)
    await runner.run_task({"id": 1, "text": "controlled file", "agent": "worker",
                           "completion_contract": {"mode": "action", "files": [{"path": "result.txt", "contains": "verified"}]}})
    finished = [call.args for call in execute.call_args_list if "UPDATE runs SET status=" in call.args[0]]
    assert finished[-1][2] == "done" if perform_write else finished[-1][2] != "done"
    assert target.exists() is perform_write
    from bossman_shared.reasoning_protocol import reasoning_protocol_prompt
    for invocation in model.call_args_list:
        outbound = invocation.args[1]
        system = outbound[0]["content"]
        assert system.startswith("KEEP EXISTING SAFETY POLICY.")
        assert system.count(reasoning_protocol_prompt()) == 1
        assert any(m["role"] == "user" and "controlled file" in m["content"] for m in outbound)


@pytest.mark.parametrize("change", ["args", "target", "expected", "actor", "plan"])
def test_compound_resume_rejects_changed_ir(tmp_path, monkeypatch, protection, change):
    from bossman_v3.execution import CompoundRunner, PlanStep
    from bossman_v3.contracts import TypedAction
    from bossman_v3.memory import TaskJournal
    from bossman_v3.organization.bridges import step_to_dict
    from test_v3_compound_resume import _agent, _Executor
    args = {"step_id": "s1"}
    host, mission, _, _ = fixture_host(tmp_path, executor="worker", run="j1", action="proj.step", args=args)
    plan = [PlanStep("s1", "write", TypedAction("proj.step", args))]
    guard.install("test", host)
    guard.enroll("compound", "j1", "j1", asdict(mission), trusted_ir=asdict(mission),
                 profile="test", plan=[step_to_dict(s) for s in plan])
    actor = "worker"
    if change == "actor": actor = "someone-else"
    elif change == "plan": plan[0] = replace(plan[0], guard="unapproved")
    else:
        effect = mission.effects[0]
        obligation = mission.obligations[0]
        if change == "args": effect = replace(effect, args_digest=digest({"step_id": "s2"}))
        if change == "target":
            effect = replace(effect, target="changed")
            obligation = replace(obligation, target="changed")
        if change == "expected": obligation = replace(obligation, expected_digest=digest("different tree"))
        altered = replace(mission, effects=(effect,), obligations=(obligation,))
        host.call(lambda rt: rt.store.db.execute("UPDATE missions SET payload=?", (__import__("json").dumps(asdict(altered)),)))
    journal = TaskJournal.start(task_id="j1", root=tmp_path / "journals", plan=[("s1", "write")])
    executor = _Executor()
    result = CompoundRunner(_agent(executor), journal, model=actor).run(plan)
    assert not result.completed and not executor.seen


def test_real_local_fleet_file_with_reality(tmp_path, protection):
    from test_v3_fleet_e2e import Stack, _contract
    stack = Stack(tmp_path)
    contract = _contract(stack.world, "w1", ["result.txt"])
    target = stack.world.root / "result.txt"
    args = contract.steps[0]["action"]["args"]
    host, mission, _, _ = fixture_host(tmp_path, executor="coder", run="m1__w1", action="fs.write", args=args)
    policy = replace(host.policy, allowed_targets=(str(target),))
    host.policy = policy
    mission = replace(mission, policy_digest=policy.fingerprint,
                      obligations=(replace(mission.obligations[0], target=str(target), expected_digest=digest("1")),),
                      effects=(replace(mission.effects[0], target=str(target)),))
    guard.install("test", host)
    guard.enroll("compound", "m1__w1", "m1__w1", asdict(mission), trusted_ir=asdict(mission),
                 profile="test", plan=contract.steps)
    from bossman_v3.fleet import FleetExecutionBridge
    result = FleetExecutionBridge(stack.plane, journal_root=tmp_path / "journals").execute(contract, agent_id="coder")
    assert result.metadata["fleet"]["state"] == "VERIFIED"
    assert target.read_text() == "1" and stack.world.side_effects() == 1
