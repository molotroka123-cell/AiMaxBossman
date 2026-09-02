"""PASS3 P0-05 — AI Company: бюджет reserve/commit/release, overspend, verifier
principal, partial ≠ VERIFIED, approval digest/scope/TTL/one-time."""
from __future__ import annotations

import pytest

from bossman.company import synthetic_seo as seo
from bossman.company.model import (AgentRole, Department, ApprovalDecision, ApprovalRequirement, BudgetEnvelope, CompanyObjective,
                                   CompanyPlan, CompanyTask, KPI, VerificationOutcome, WorkResult,
                                   Workstream, task_digest)
from bossman.company.runtime import CompanyRuntime


def _plan(tasks, *, max_total=10.0, max_task=None) -> CompanyPlan:
    obj = CompanyObjective(id="obj", title="t", domain="generic", description="d", kpis=(KPI(name="k", target=1.0),))
    ws = Workstream(id="ws", name="w", department="ops")
    return CompanyPlan(objective=obj, departments=(Department(name="ops"),), roles=(AgentRole(name="r", department="ops"),),
                       workstreams=(ws,), tasks=tuple(tasks),
                       budget=BudgetEnvelope(max_total_cost=max_total, max_task_cost=max_task))


def _task(tid, cost=1.0, deps=(), kind="read", approvals=(), evidence=()):
    from bossman.company.model import TaskDependency, EvidenceRequirement
    return CompanyTask(id=tid, workstream_id="ws", title=tid, action="act", role="r", kind=kind,
                       estimated_cost=cost, dependencies=tuple(TaskDependency(upstream=d) for d in deps),
                       requires_approval=tuple(approvals),
                       evidence_requirements=tuple(EvidenceRequirement(kind="file", target=e) for e in evidence))


def _ok_verifier(task, result):
    return VerificationOutcome("VERIFIED", "fresh", evidence=("obs",))


def test_overspend_actual_cost_cannot_silently_exceed_envelope():
    """Оценка помещается, факт — нет: задача FAILED (cost overrun), бюджет закрыт,
    следующие задачи BUDGET_EXCEEDED, никакого VERIFIED."""
    t1, t2 = _task("a", cost=2.0), _task("b", cost=2.0)
    rt = CompanyRuntime(_plan([t1, t2], max_total=5.0),
                        executor=lambda t: WorkResult(t.id, True, cost=9.0 if t.id == "a" else 1.0),
                        verifier=_ok_verifier, synthetic=True, max_rounds=1)
    rep = rt.run()
    assert rep.task_states["a"] == "BUDGET_EXCEEDED" and "overrun" in {o.task_id: o for o in rep.outcomes}["a"].reason
    assert rep.task_states["b"] == "BUDGET_EXCEEDED"
    assert rep.budget["budget_exhausted"] is True and rep.budget["overruns"] == ["a"]
    assert rep.status == "PARTIAL" and rep.budget["spent"] == 9.0 and rep.budget["reserved"] == 0.0
    assert rt.executor_calls == ("a",)                     # overrun терминален: без retry


def test_cost_meter_overrides_self_reported_cost():
    t = _task("a", cost=1.0)
    rt = CompanyRuntime(_plan([t], max_total=5.0), executor=lambda t: WorkResult(t.id, True, cost=0.1),
                        cost_meter=lambda task, res: 4.5, synthetic=True)
    rep = rt.run()
    assert rep.budget["spent"] == 4.5                      # измеритель, не самоотчёт
    rt2 = CompanyRuntime(_plan([t], max_total=3.0), executor=lambda t: WorkResult(t.id, True, cost=0.1),
                         cost_meter=lambda task, res: 4.5, synthetic=True)
    assert rt2.run().task_states["a"] == "BUDGET_EXCEEDED"  # факт > конверт → overrun, терминально


def test_reservation_counts_against_budget_and_is_released_on_failure():
    """Резерв учитывается до коммита; при исключении исполнителя резерв снимается."""
    t = _task("a", cost=4.0)
    calls = {"n": 0}

    def boom(task):
        calls["n"] += 1
        raise RuntimeError("x")
    rt = CompanyRuntime(_plan([t], max_total=5.0), executor=boom, synthetic=True, max_rounds=2)
    rep = rt.run()
    assert rep.task_states["a"] == "FAILED" and rep.budget["reserved"] == 0.0 and rep.budget["spent"] == 0.0
    assert calls["n"] == 2                                  # retry прошёл (резерв освобождён)
    # concurrent-style reservation: две задачи по 3.0 при конверте 5.0 — вторая не помещается
    # уже на этапе резерва (spent+reserved), даже если первая ещё "в полёте"
    seen = []

    def exec_check(task):
        seen.append(rt2.state.reserved)
        return WorkResult(task.id, True, cost=3.0)
    rt2 = CompanyRuntime(_plan([_task("a", 3.0), _task("b", 3.0)], max_total=5.0),
                         executor=exec_check, synthetic=True, max_rounds=1)
    rep2 = rt2.run()
    assert seen[0] == 3.0 and rep2.task_states == {"a": "DONE", "b": "BUDGET_EXCEEDED"}


def test_same_principal_verifier_is_not_evidence():
    t = _task("a", evidence=("f",))
    rt = CompanyRuntime(_plan([t]), executor=lambda t: WorkResult(t.id, True), verifier=_ok_verifier,
                        executor_principal="agent:planner", verifier_principal="agent:planner", synthetic=True)
    rep = rt.run()
    assert rep.status == "UNVERIFIED"
    assert "self-verification" in {o.task_id: o for o in rep.outcomes}["a"].verification.reason
    rt2 = CompanyRuntime(_plan([t]), executor=lambda t: WorkResult(t.id, True), verifier=_ok_verifier,
                         executor_principal="agent:planner", verifier_principal="tool:site-reader", synthetic=True)
    assert rt2.run().status == "VERIFIED"


def test_partial_objective_is_never_verified():
    """Одна задача VERIFIED, другая DENIED → PARTIAL/UNVERIFIED, run-record не VERIFIED."""
    a = _task("a", evidence=("f",))
    b = _task("b", kind="publish", approvals=(ApprovalRequirement(kind="publish"),))
    rt = CompanyRuntime(_plan([a, b]), executor=lambda t: WorkResult(t.id, True), verifier=_ok_verifier,
                        verifier_principal="tool:reader", synthetic=True)
    rep = rt.run()
    assert rep.task_states == {"a": "DONE", "b": "DENIED"}
    assert rep.status == "PARTIAL" and rep.completion == "PARTIAL"
    run_rec = rep.learning_records[-1]
    assert run_rec["learning_status"] != "VERIFIED"


def test_approval_requires_digest_scope_ttl_and_is_one_time():
    plan = _plan([_task("p", kind="publish", approvals=(ApprovalRequirement(kind="publish"),))])
    t = plan.tasks[0]
    good = dict(digest=task_digest("obj", t), scope="obj", nonce="n1")
    cases = {
        "no digest": ApprovalDecision(True, "human:o", scope="obj", nonce="n"),
        "other task digest": ApprovalDecision(True, "human:o", digest=task_digest("obj", _task("q")), scope="obj", nonce="n"),
        "other scope": ApprovalDecision(True, "human:o", digest=good["digest"], scope="other", nonce="n"),
        "expired": ApprovalDecision(True, "human:o", expires_at=1.0, **good),
        "no nonce": ApprovalDecision(True, "human:o", digest=good["digest"], scope="obj"),
    }
    for name, d in cases.items():
        rt = CompanyRuntime(plan, executor=lambda t: WorkResult(t.id, True), approval_gate=lambda task, req: d,
                            synthetic=True, clock=lambda: 100.0)
        rep = rt.run()
        assert rep.task_states["p"] == "DENIED", name
        assert rt.executor_calls == (), name
    # валидное одобрение — один раз; replay того же nonce на retry → DENIED
    calls = {"n": 0}

    def exec_fail_once(task):
        calls["n"] += 1
        return WorkResult(task.id, calls["n"] > 1)
    plan2 = _plan([CompanyTask(id="p", workstream_id="ws", title="p", action="act", role="r", kind="publish",
                               requires_approval=(ApprovalRequirement(kind="publish"),), max_attempts=2)])
    valid = ApprovalDecision(True, "human:o", expires_at=200.0, **good)
    rt = CompanyRuntime(plan2, executor=exec_fail_once, approval_gate=lambda task, req: valid,
                        synthetic=True, clock=lambda: 100.0, max_rounds=2)
    rep = rt.run()
    assert calls["n"] == 1 and rep.task_states["p"] == "DENIED"
    assert "replay" in {o.task_id: o for o in rep.outcomes}["p"].reason
