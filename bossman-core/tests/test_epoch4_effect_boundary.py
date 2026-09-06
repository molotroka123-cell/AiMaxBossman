"""Continuity: an approval/guard cannot change the action or cached authority.

Real temporary-file effects; deterministic policy fixtures, not model acceptance.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bossman_v3.computer_agent.agent import (
    ApprovalDeniedError, PolicyDeniedError, UniversalComputerAgent, UnsafeActionError,
)
from bossman_v3.contracts import (
    ApprovalDecision, ExecutionReceipt, Observation, PolicyDecision,
    SideEffectClass, TypedAction, VerificationResult,
)


def stack(tmp_path, *, requires_approval=False, approval_callback=None):
    authority = {"allowed": True, "ask": requires_approval, "calls": 0}

    class Policy:
        def authorize(self, action, context):
            authority["calls"] += 1
            return PolicyDecision(authority["allowed"], authority["ask"], "current policy")

    class Approval:
        def request(self, action, decision, context):
            if approval_callback:
                approval_callback(action, authority)
            return ApprovalDecision(True, "owner-approval")

    class Executor:
        calls = 0
        def supports(self, name):
            return name == "fixture.write"
        def execute(self, action):
            self.calls += 1
            path = tmp_path / action.args["name"]
            path.write_text(action.args["payload"]["text"])
            now = datetime.now(timezone.utc)
            return ExecutionReceipt(action.action_type, now, now, "fixture-effect")

    class Observer:
        def observe_fresh(self, action, receipt):
            path = tmp_path / action.args["name"]
            return Observation(datetime.now(timezone.utc), "fs", {"text": path.read_text()})

    class Verifier:
        def verify(self, action, receipt, observation):
            return VerificationResult(observation.state["text"] == action.args["payload"]["text"])

    executor = Executor()
    agent = UniversalComputerAgent(Policy(), Approval(), executor, Observer(), Verifier())
    action = TypedAction("fixture.write", {"name": "safe.txt", "payload": {"text": "wanted"}},
                         ("project.write",), SideEffectClass.IDEMPOTENT_WRITE)
    return SimpleNamespace(agent=agent, action=action, authority=authority, executor=executor)


def test_authorization_is_rechecked_after_owner_approval(tmp_path):
    def revoke(action, authority):
        authority["allowed"] = False
    s = stack(tmp_path, requires_approval=True, approval_callback=revoke)
    with pytest.raises(PolicyDeniedError):
        s.agent.run(s.action)
    assert s.executor.calls == 0
    assert not (tmp_path / "safe.txt").exists()


def test_nested_argument_mutation_by_approval_is_denied(tmp_path):
    def mutate(action, authority):
        action.args["payload"]["text"] = "substituted"
    s = stack(tmp_path, requires_approval=True, approval_callback=mutate)
    with pytest.raises(UnsafeActionError):
        s.agent.run(s.action)
    assert s.executor.calls == 0
    assert s.action.args["payload"]["text"] == "wanted"


def test_guard_can_revoke_before_effect(tmp_path):
    s = stack(tmp_path)
    @contextmanager
    def guard():
        s.authority["allowed"] = False
        yield
    with pytest.raises(PolicyDeniedError):
        s.agent.run(s.action, {"execution_guard": guard})
    assert s.executor.calls == 0


def test_new_ask_policy_inside_guard_does_not_implicitly_approve(tmp_path):
    s = stack(tmp_path)
    @contextmanager
    def guard():
        s.authority["ask"] = True
        yield
    with pytest.raises(ApprovalDeniedError):
        s.agent.run(s.action, {"execution_guard": guard})
    assert s.executor.calls == 0


def test_caller_mutating_original_action_cannot_change_dispatched_copy(tmp_path):
    s = stack(tmp_path)
    @contextmanager
    def guard():
        s.action.args["name"] = "other.txt"
        s.action.args["payload"]["text"] = "substituted"
        yield
    result = s.agent.run(s.action, {"execution_guard": guard})
    assert result.verification.passed
    assert (tmp_path / "safe.txt").read_text() == "wanted"
    assert not (tmp_path / "other.txt").exists()


def test_snapshot_in_outcome_does_not_alias_caller(tmp_path):
    s = stack(tmp_path)
    result = s.agent.run(s.action)
    s.action.args["payload"]["text"] = "later mutation"
    assert result.action.args["payload"]["text"] == "wanted"
    assert s.authority["calls"] >= 2


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_unbindable_action_never_reaches_executor(tmp_path, invalid):
    s = stack(tmp_path)
    s.action.args["payload"]["text"] = invalid
    with pytest.raises(UnsafeActionError):
        s.agent.run(s.action)
    assert s.executor.calls == 0
