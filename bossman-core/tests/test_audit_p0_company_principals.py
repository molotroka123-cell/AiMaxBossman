"""Audit P0: AI Company approver/verifier are trusted typed principals.
model:* / agent:* can never approve; an alias of the executor never verifies."""
from __future__ import annotations

import pytest

from bossman.company.runtime import untrusted_approver_reason, verifier_dependency_reason
from bossman.deep_fix import Principal


@pytest.mark.parametrize("approver", ["model:gpt-5", "agent:planner", "MODEL:qwen", "executor", "", "llm:x"])
def test_model_or_agent_principals_cannot_approve(approver):
    assert untrusted_approver_reason(approver)


@pytest.mark.parametrize("approver", ["human:owner", "policy:default-allow"])
def test_typed_trusted_prefixes_are_accepted_by_default(approver):
    assert untrusted_approver_reason(approver) == ""


def test_explicit_trusted_set_is_a_whitelist():
    trusted = frozenset({"human:owner"})
    assert untrusted_approver_reason("human:owner", trusted) == ""
    assert untrusted_approver_reason("human:intern", trusted)
    assert untrusted_approver_reason("model:gpt", trusted)          # never, even if listed
    assert untrusted_approver_reason("model:gpt", frozenset({"model:gpt"}))


def test_alias_verifier_is_the_executor():
    assert verifier_dependency_reason("verifier:qwen-14b", "qwen-14b")
    assert verifier_dependency_reason("", "qwen-14b")
    assert verifier_dependency_reason("human:qa", "qwen-14b") == ""


def test_typed_principals_use_independence_classes():
    executor = Principal(principal_id="agent:exec#r1", model_id="qwen-14b", role="executor", run_id="r1")
    same_model = Principal(principal_id="verifier:other", model_id="QWEN-14B", role="verifier", run_id="r2",
                           independence_class="cross_model")
    assert "not independent" in verifier_dependency_reason(same_model, executor)
    human = Principal(principal_id="human:qa", role="human", run_id="r2", independence_class="human")
    assert verifier_dependency_reason(human, executor) == ""


def test_runtime_denies_model_approver_end_to_end():
    from tests.test_pass3_company_integrity import _plan, _task  # noqa: WPS433
    from bossman.company.model import ApprovalDecision, ApprovalRequirement, task_digest
    from bossman.company.runtime import CompanyRuntime, WorkResult

    plan = _plan([_task("p", kind="publish", approvals=(ApprovalRequirement(kind="publish"),))])
    t = plan.tasks[0]
    fake = ApprovalDecision(True, "model:gpt-5", "looks fine", digest=task_digest("obj", t), scope="obj", nonce="n1")
    rt = CompanyRuntime(plan, executor=lambda task: WorkResult(task.id, True), approval_gate=lambda task, req: fake,
                        synthetic=True, clock=lambda: 100.0)
    rep = rt.run()
    assert rep.task_states["p"] == "DENIED" and rt.executor_calls == ()
    real = ApprovalDecision(True, "human:owner", "ok", digest=task_digest("obj", t), scope="obj", nonce="n2")
    rt2 = CompanyRuntime(plan, executor=lambda task: WorkResult(task.id, True), approval_gate=lambda task, req: real,
                         synthetic=True, clock=lambda: 100.0)
    assert rt2.run().task_states["p"] != "DENIED"
