"""Canonical UCA/CompoundRunner integration; actual fixture file is the oracle."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
from types import MappingProxyType

import pytest
from bossman_v3.computer_agent.agent import (UniversalComputerAgent, ApprovalDeniedError,
    PolicyDeniedError, UnsafeActionError, StaleObservationError)
from bossman_v3.contracts import (TypedAction, SideEffectClass, PolicyDecision, ApprovalDecision,
    ExecutionReceipt, Observation, VerificationResult)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory import TaskJournal
from bossman_v3.visual_state.action_state import SemanticActionStateGuard
from bossman_v3.visual_state.dispatch import BoundVisualDispatch
from bossman_v3.visual_state.models import StateFragment, StateIdentity


class Ports:
    def __init__(self, target):
        self.target = target
        self.policies = []
        self.calls = []
        self.approval_hook = lambda: None
        self.verifier_hook = lambda: None
        self.approval_count = 0
    def authorize(self, action, context):
        self.calls.append("policy")
        return self.policies.pop(0) if self.policies else PolicyDecision(True)
    def supports(self, name): return name == "file.write"
    def request(self, action, policy, context):
        self.approval_count += 1; self.approval_hook()
        return ApprovalDecision(True, approval_id="owner-approval")
    def execute(self, action):
        self.calls.append("effect")
        started = datetime.now(timezone.utc)
        self.target.write_text(action.args["content"])
        return ExecutionReceipt(action.action_type, started, datetime.now(timezone.utc), "fixture-effect")
    def observe_fresh(self, action, receipt):
        return Observation(datetime.now(timezone.utc), "fs", {"content": self.target.read_text()})
    def verify(self, action, receipt, obs):
        self.verifier_hook()
        return VerificationResult(obs.state["content"] == action.args["content"])
    def agent(self, **kw): return UniversalComputerAgent(self, self, self, self, self, **kw)


def action():
    return TypedAction("file.write", {"content": "exact bytes", "expect": {"exists": True}},
                       side_effect=SideEffectClass.IDEMPOTENT_WRITE)


def test_policy_revoked_inside_guard_prevents_real_effect(tmp_path):
    p = Ports(tmp_path / "effect")
    @contextmanager
    def guard():
        p.policies = [PolicyDecision(False, reason="revoked")]
        yield
    with pytest.raises(PolicyDeniedError, match="revoked"):
        p.agent().run(action(), {"execution_guard": guard})
    assert not p.target.exists()


def test_new_ask_is_not_treated_as_previous_auto(tmp_path):
    p = Ports(tmp_path / "effect")
    p.policies = [PolicyDecision(True), PolicyDecision(True, requires_approval=True)]
    with pytest.raises(ApprovalDeniedError, match="now requires"):
        p.agent().run(action())
    assert p.approval_count == 0 and not p.target.exists()


def test_prior_owner_approval_preserved_without_double_consumption(tmp_path):
    p = Ports(tmp_path / "effect")
    p.policies = [PolicyDecision(True, requires_approval=True)] * 2
    outcome = p.agent().run(action())
    assert p.approval_count == 1 and outcome.approval_id == "owner-approval"
    assert outcome.verification.passed and p.target.read_text() == "exact bytes"


@pytest.mark.parametrize("field", ["content", "expect"])
def test_mutation_during_approval_is_rejected(tmp_path, field):
    p = Ports(tmp_path / "effect"); a = action()
    p.policies = [PolicyDecision(True, requires_approval=True)]
    def mutate():
        if field == "content": a.args["content"] = "different goal"
        else: a.args["expect"]["exists"] = False
    p.approval_hook = mutate
    with pytest.raises(UnsafeActionError, match="changed"):
        p.agent().run(a)
    assert not p.target.exists()


def test_post_effect_expectation_mutation_never_becomes_verified(tmp_path):
    p = Ports(tmp_path / "effect"); a = action()
    p.verifier_hook = lambda: a.args["expect"].update(exists=False)
    with pytest.raises(UnsafeActionError, match="changed"):
        p.agent().run(a)
    assert p.target.read_text() == "exact bytes"  # effect happened, not a completion claim


def test_reaction_frozen_nested_arguments_are_supported(tmp_path):
    p = Ports(tmp_path / "effect")
    a = replace(action(), args=MappingProxyType({"content": "exact bytes",
                                                "expect": MappingProxyType({"exists": True})}))
    assert p.agent().run(a).verification.passed


def test_policy_denial_does_not_leave_phantom_irreversible_intent(tmp_path):
    p = Ports(tmp_path / "effect")
    a = replace(action(), side_effect=SideEffectClass.IRREVERSIBLE)
    j = TaskJournal.start(task_id="boundary", plan=[("one", "write")], root=tmp_path / "journals")
    p.policies = [PolicyDecision(True), PolicyDecision(False)]
    runner = CompoundRunner(p.agent(), j)
    result = runner.run([PlanStep("one", "write", a)])
    assert not result.completed and not p.target.exists()
    assert not j.steps[0].in_flight
    # The refused action did NOT happen. A fresh authorized attempt may run.
    assert runner.run([PlanStep("one", "write", a)]).completed
    assert p.calls.count("effect") == 1


def fragments(revision=1):
    return [StateFragment("accessibility", datetime.now(timezone.utc), {"button": "save", "enabled": True},
                          "fixture-accessibility", identity=StateIdentity("editor", "w1", "doc1", 0, revision))]


@pytest.mark.parametrize("drift", [False, True])
def test_bound_visual_guard_is_actually_in_execution_path(tmp_path, drift):
    p = Ports(tmp_path / "effect"); a = action()
    g = SemanticActionStateGuard()
    binding = g.bind(a, fragments(), required_state={"button": "save", "enabled": True})
    owned = []
    @contextmanager
    def input_guard():
        owned.append(True)
        try: yield
        finally: owned.pop()
    def capture(a):
        assert owned, "capture must run INSIDE the existing input guard"
        return fragments(2 if drift else 1)
    agent = p.agent(pre_dispatch=BoundVisualDispatch(binding, capture, g))
    if drift:
        with pytest.raises(StaleObservationError, match="re-observation"):
            agent.run(a, {"execution_guard": input_guard})
        assert not p.target.exists()
    else:
        assert agent.run(a, {"execution_guard": input_guard}).verification.passed
        assert p.target.read_text() == "exact bytes"


def test_visual_dispatch_without_input_guard_fails_closed(tmp_path):
    p = Ports(tmp_path / "effect"); a = action(); g = SemanticActionStateGuard()
    binding = g.bind(a, fragments(), required_state={"enabled": True})
    with pytest.raises(StaleObservationError, match="host-owned"):
        p.agent(pre_dispatch=BoundVisualDispatch(binding, lambda a: fragments(), g)).run(a)
    assert not p.target.exists()


def test_record_intent_is_after_checks_but_before_effect(tmp_path):
    p = Ports(tmp_path / "effect")
    def intent():
        assert p.calls == ["policy", "policy"]
        assert not p.target.exists()
        p.calls.append("intent")
    p.agent().run(action(), {"record_effect_intent": intent})
    assert p.calls == ["policy", "policy", "intent", "effect"]
