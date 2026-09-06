"""Live BCC boundaries: current permissions, full approval identity, revoke race.

Reuses real SQLite, canonical Approvals and terminal fixture. No cloud model.
"""
from contextlib import contextmanager
from dataclasses import replace

import pytest
import sqlalchemy as sa

from bossman_v3.computer_agent.agent import ApprovalDeniedError, PolicyDeniedError
from bossman_v3.contracts import TypedAction
from bossman_v3.adapters.command_center import _preview
from test_v3_command_center_adapters import live, _write


def _grant(live, action):
    agent = live.agent_()
    with pytest.raises(ApprovalDeniedError):
        agent.run(action)
    assert live.approve_all_pending() == 1
    return agent


def test_current_permissions_override_adapter_creation_snapshot(live):
    from bcc.db import agents
    action = _write(live.work, "forbidden.txt")
    agent = _grant(live, action)
    async def revoke():
        async with live.svc.db.session() as session:
            await session.execute(sa.update(agents).where(agents.c.id == live.agent["id"]).values(
                permissions={"tool_rules": [{"tool": "terminal.run", "resource": "*", "effect": "deny"}]}))
            await session.commit()
    live.rt.call(revoke())
    with pytest.raises(PolicyDeniedError):
        agent.run(action)
    assert not (live.work / "forbidden.txt").exists()
    assert live.tool_calls() == []


def test_revocation_between_approval_lookup_and_effect_prevents_real_write(live):
    action = _write(live.work, "revoked.txt")
    agent = _grant(live, action)
    approved = live.rt.call(live.svc.approvals.list(status="approved"))[0]
    @contextmanager
    def guard():
        row = live.rt.call(live.svc.approvals.revoke(approved["id"]))
        assert row["status"] == "revoked"
        yield
    with pytest.raises(ApprovalDeniedError):
        agent.run(action, {"execution_guard": guard})
    assert not (live.work / "revoked.txt").exists()


def test_truncated_command_tail_never_aliases_an_approval(live):
    a = TypedAction("terminal.run", {"command": " " * 550 + "echo SAFE", "cwd": str(live.work)})
    b = replace(a, args={**a.args, "command": " " * 550 + "echo CHANGED"})
    assert _preview(a, live.task["id"]) != _preview(b, live.task["id"])


def test_changed_expectation_never_aliases_approval(live):
    a = _write(live.work, "wanted.txt")
    b = replace(a, args={**a.args, "expect": {"kind": "file", "target": "elsewhere", "expect": {"exists": True}}})
    assert _preview(a, live.task["id"]) != _preview(b, live.task["id"])


def test_same_task_different_run_cannot_reuse_approval(live):
    from bossman_v3.adapters.command_center import build_agent
    a = _write(live.work, "run-bound.txt")
    _grant(live, a)
    other_run = live.rt.call(live.svc.engine.enqueue(live.task["id"]))
    other = build_agent(live.rt, live.svc, task=live.task, agent=live.agent, run_id=other_run)
    with pytest.raises(ApprovalDeniedError):
        other.run(a)
    assert not (live.work / "run-bound.txt").exists()


def test_second_consumption_denied_and_verified_receipt_preserved(live):
    a = _write(live.work, "once.txt")
    agent = _grant(live, a)
    result = agent.run(a)
    assert result.verification.passed
    assert len(live.tool_calls()) == 1
    with pytest.raises(ApprovalDeniedError):
        agent.run(a)
    assert len(live.tool_calls()) == 1


def test_reregistered_tool_requires_a_fresh_approval(live):
    from bcc.tools import REGISTRY
    action = _write(live.work, "generation.txt")
    agent = _grant(live, action)
    spec = REGISTRY.get("terminal.run")
    # canonical generation changes, even when the tool name stays the same
    REGISTRY.register(spec)
    with pytest.raises(ApprovalDeniedError):
        agent.run(action)
    assert not (live.work / "generation.txt").exists()
