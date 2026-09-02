"""AI Company Mode foundation: флаг OFF по умолчанию, роль ≠ полномочие,
DAG/бюджет/гейты, свежая верификация против самоотчёта, learning records по
schemas/learning_fix_case.schema.json (валидатор — корневой пакет `learning`)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from learning import validate as learning_validate  # noqa: E402

from bossman import company  # noqa: E402
from bossman.company import synthetic_seo as seo  # noqa: E402
from bossman.company.model import (AgentRole, ApprovalDecision, ApprovalRequirement, BudgetEnvelope,  # noqa: E402
                                   CompanyModeDisabled, CompanyObjective, CompanyPlan, CompanyTask,
                                   Department, EvidenceRequirement, TaskDependency, VerificationOutcome,
                                   WorkResult, Workstream)
from bossman.company.planner import over_budget, plan_objective  # noqa: E402
from bossman.company.runtime import CompanyRuntime, deny_all_gate  # noqa: E402

EXPECTED_DAG = {
    "seo-audit": (), "seo-fix-titles": ("seo-audit",), "seo-fix-meta": ("seo-audit",),
    "seo-fix-alt": ("seo-audit",), "seo-rescore": ("seo-fix-titles", "seo-fix-meta", "seo-fix-alt"),
    "seo-publish": ("seo-rescore",),
}


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv(company.FLAG, raising=False)


# ---- флаг ------------------------------------------------------------------------
def test_flag_off_by_default(monkeypatch):
    assert company.enabled() is False
    monkeypatch.setenv(company.FLAG, "1")
    assert company.enabled() is True


def test_runtime_refuses_when_flag_off_and_not_synthetic():
    site = seo.default_site()
    calls = []
    def executor(task):
        calls.append(task.id)
        return WorkResult(task.id, True)
    rt = CompanyRuntime(seo.build_plan(), executor=executor, synthetic=False)
    with pytest.raises(CompanyModeDisabled):
        rt.run()
    assert calls == [] and site.writes == 0


def test_runtime_runs_when_flag_on(monkeypatch):
    monkeypatch.setenv(company.FLAG, "true")
    site = seo.default_site()
    rt = CompanyRuntime(seo.build_plan(), executor=seo.make_executor(site), verifier=seo.make_verifier(site),
                        kpi_reader=seo.make_kpi_reader(site), synthetic=False)
    assert rt.run().status == "PARTIAL"          # P0-05: publish DENIED ⇒ прогон не VERIFIED целиком


# ---- план / DAG ----------------------------------------------------------------
def test_planner_is_deterministic_and_builds_expected_dag():
    p1, p2 = seo.build_plan(), seo.build_plan()
    assert p1 == p2
    assert p1.dag() == EXPECTED_DAG
    order = [t.id for t in p1.ordered()]
    assert order[0] == "seo-audit" and order[-1] == "seo-publish"
    assert order.index("seo-rescore") > max(order.index(x) for x in ("seo-fix-titles", "seo-fix-meta", "seo-fix-alt"))
    assert {t.id: t.role for t in p1.tasks} == {
        "seo-audit": "seo_analyst", "seo-fix-titles": "content_editor", "seo-fix-meta": "content_editor",
        "seo-fix-alt": "web_engineer", "seo-rescore": "seo_analyst", "seo-publish": "release_manager"}
    for t in p1.tasks:  # каждая задача несёт оба поля
        assert isinstance(t.requires_approval, tuple) and isinstance(t.evidence_requirements, tuple)
    assert p1.by_id()["seo-publish"].gated and p1.by_id()["seo-publish"].kind == "publish"


def test_planner_rejects_unknown_domain():
    obj = CompanyObjective("o", "x", domain="quantum-finance")
    with pytest.raises(ValueError):
        plan_objective(obj, BudgetEnvelope(10))


def _mini_plan(tasks, budget=BudgetEnvelope(100.0)):
    return CompanyPlan(objective=CompanyObjective("o", "mini", "generic"), budget=budget,
                       departments=(Department("ops"),), roles=(AgentRole("op", "ops"),),
                       workstreams=(Workstream("ws", "ws", "ops"),), tasks=tuple(tasks))


def test_cycle_in_dag_raises_value_error():
    a = CompanyTask("a", "ws", "A", "x.a", "op", dependencies=(TaskDependency("b"),))
    b = CompanyTask("b", "ws", "B", "x.b", "op", dependencies=(TaskDependency("a"),))
    with pytest.raises(ValueError, match="cycle"):
        _mini_plan([a, b]).ordered()
    with pytest.raises(ValueError):
        _mini_plan([CompanyTask("a", "ws", "A", "x.a", "op", dependencies=(TaskDependency("ghost"),))]).ordered()
    rt = CompanyRuntime(_mini_plan([a, b]), executor=lambda t: WorkResult(t.id, True), synthetic=True)
    with pytest.raises(ValueError):
        rt.run()


def test_gated_kind_without_approval_requirement_is_invalid():
    t = CompanyTask("pub", "ws", "P", "x.publish", "op", kind="publish")
    with pytest.raises(ValueError, match="requires_approval"):
        _mini_plan([t]).validate()
    with pytest.raises(ValueError):
        ApprovalRequirement("marketing")


# ---- синтетический E2E ------------------------------------------------------------
def test_synthetic_e2e_report():
    report, site, rt = seo.run_demo()
    assert report.objective_title == seo.OBJECTIVE_TITLE
    assert report.objective_id == "obj-synthetic-seo"
    assert dict(report.dag) == EXPECTED_DAG
    assert report.assignments["seo-publish"] == "release_manager"
    # состояния
    assert report.task_states == {"seo-audit": "DONE", "seo-fix-titles": "DONE", "seo-fix-meta": "DONE",
                                  "seo-fix-alt": "DONE", "seo-rescore": "DONE", "seo-publish": "DENIED"}
    assert report.denied == ("seo-publish",)
    assert report.status == "PARTIAL" and report.completion == "PARTIAL"   # P0-05: DENIED-задача ⇒ не VERIFIED
    # KPI до/после — свежее чтение сайта, а не заявления исполнителя
    assert report.kpi_before["seo_readiness"] == 62.5
    assert report.kpi_after["seo_readiness"] == 100.0
    s = {k["name"]: k for k in report.kpi_summary}
    assert s["seo_readiness"]["improved"] and s["seo_readiness"]["met"] is True
    assert s["open_issues"]["improved"] and s["open_issues"]["after"] == 0.0
    # трасса исполнения
    events = [(e["task_id"], e["event"]) for e in report.trace]
    assert events[0][1] == "plan.accepted"
    assert ("seo-audit", "task.start") in events and ("seo-publish", "task.denied") in events
    assert ("seo-publish", "task.start") not in events
    assert events.index(("seo-audit", "task.done")) < events.index(("seo-fix-titles", "task.start"))
    # доказательства на задачу
    ev = report.evidence()
    assert any("every page has title" in e for e in ev["seo-fix-titles"])
    assert any("every image has alt" in e for e in ev["seo-fix-alt"])
    assert ev["seo-publish"] == ()
    # ничего не опубликовано, гейт спросили, executor для publish не вызывался
    assert site.published is False
    assert "seo-publish" not in rt.executor_calls
    assert report.rounds == 1
    assert report.budget["spent"] == 8.0 and report.budget["spent"] <= report.budget["max_total_cost"]
    d = report.to_dict()
    assert d["status"] == "PARTIAL" and d["denied"] == ["seo-publish"]


def test_synthetic_e2e_is_deterministic():
    r1, _, _ = seo.run_demo()
    r2, _, _ = seo.run_demo()
    assert r1.to_dict() == r2.to_dict()


def test_learning_records_validate_and_encode_denial():
    report, _, _ = seo.run_demo()
    recs = {r["task_id"]: r for r in report.learning_records}
    assert len(recs) == 7
    for tid, r in recs.items():
        assert learning_validate(r) == [], (tid, learning_validate(r))
    assert recs["obj-synthetic-seo/seo-fix-titles"]["learning_status"] == "VERIFIED"
    assert recs["obj-synthetic-seo/seo-fix-titles"]["verified_by"] == ["verifier:fresh_site_verifier"]
    denied = recs["obj-synthetic-seo/seo-publish"]
    assert denied["learning_status"] == "PARTIAL"
    assert denied["outcome"] == "ACCEPTED_RISK_REQUIRES_OWNER"
    assert denied["symptom"].startswith("denied by approval gate")
    assert denied["verified_by"] == []
    run = recs["obj-synthetic-seo/run"]
    assert run["learning_status"] == "PARTIAL" and run["outcome"] == "PARTIAL"   # P0-05: частичный прогон — не VERIFIED
    assert any("seo_readiness: 62.5 -> 100.0" in e for e in run["evidence"])
    assert any("seo-publish DENIED" in x for x in run["limitations"])


# ---- полномочия ------------------------------------------------------------------
def test_role_name_confers_no_authority():
    """Переименование роли publish-задачи в «cfo»/«admin» ничего не меняет:
    решает только гейт."""
    plan = seo.build_plan()
    tasks = []
    for t in plan.tasks:
        if t.id == "seo-publish":
            t = CompanyTask(t.id, t.workstream_id, t.title, t.action, "chief_publishing_officer", kind=t.kind,
                            dependencies=t.dependencies, requires_approval=t.requires_approval,
                            evidence_requirements=t.evidence_requirements, estimated_cost=t.estimated_cost)
        tasks.append(t)
    plan2 = CompanyPlan(plan.objective, plan.budget, plan.departments,
                        plan.roles + (AgentRole("chief_publishing_officer", "compliance"),),
                        plan.workstreams, tuple(tasks))
    site = seo.default_site()
    rt = CompanyRuntime(plan2, executor=seo.make_executor(site), verifier=seo.make_verifier(site),
                        kpi_reader=seo.make_kpi_reader(site), synthetic=True)
    report = rt.run()
    assert report.task_states["seo-publish"] == "DENIED" and site.published is False
    assert not hasattr(AgentRole("cfo", "finance"), "can_spend")


def test_default_gate_denies_and_records_approver():
    t = seo.build_plan().by_id()["seo-publish"]
    d = deny_all_gate(t, t.requires_approval[0])
    assert d.approved is False and d.approver == "policy:default-deny"


def test_explicit_external_approval_lets_gated_task_run():
    from bossman.company.model import task_digest
    plan = seo.build_plan()

    def owner_gate(task, req):
        # P0-05: одобрение действительно только с canonical digest, scope и одноразовым nonce
        return ApprovalDecision(True, "human:owner", f"approved {req.kind} for {task.id}",
                                digest=task_digest(plan.objective.id, task), scope=plan.objective.id,
                                nonce=f"nonce-{task.id}-{req.kind}")
    report, site, rt = seo.run_demo(approval_gate=owner_gate)
    assert report.task_states["seo-publish"] == "DONE" and site.published is True
    assert report.completion == "COMPLETE" and report.status == "VERIFIED"
    o = {x.task_id: x for x in report.outcomes}["seo-publish"]
    assert o.approval.approver == "human:owner"


@pytest.mark.parametrize("gate", [lambda t, r: True, lambda t, r: None, lambda t, r: "yes"])
def test_gate_returning_non_decision_is_denial(gate):
    report, site, _ = seo.run_demo(approval_gate=gate)
    assert report.task_states["seo-publish"] == "DENIED" and site.published is False


def test_gate_raising_is_denial():
    def broken(t, r):
        raise RuntimeError("gate down")
    report, site, _ = seo.run_demo(approval_gate=broken)
    assert report.task_states["seo-publish"] == "DENIED" and site.published is False
    assert "gate raised" in {x.task_id: x for x in report.outcomes}["seo-publish"].reason


# ---- бюджет ------------------------------------------------------------------------
def test_budget_exceeded_skips_task_and_downstream():
    budget = BudgetEnvelope(max_total_cost=20.0, max_task_cost=1.5)   # fix-задачи стоят 2.0
    plan = seo.build_plan(budget)
    assert set(over_budget(plan)) == {"seo-fix-titles", "seo-fix-meta", "seo-fix-alt"}
    report, site, rt = seo.run_demo(budget=budget)
    st = report.task_states
    assert st["seo-audit"] == "DONE"
    assert st["seo-fix-titles"] == st["seo-fix-meta"] == st["seo-fix-alt"] == "BUDGET_EXCEEDED"
    assert st["seo-rescore"] == "SKIPPED" and st["seo-publish"] == "SKIPPED"
    assert site.writes == 0 and rt.executor_calls == ("seo-audit",)
    assert report.status == "PARTIAL" and report.completion == "PARTIAL"   # P0-05: аудит верифицирован, но BUDGET_EXCEEDED ⇒ не VERIFIED
    recs = {r["task_id"]: r for r in report.learning_records}
    assert recs["obj-synthetic-seo/seo-fix-alt"]["outcome"] == "BLOCKED_ENV"
    assert all(learning_validate(r) == [] for r in report.learning_records)


def test_total_budget_envelope_stops_later_tasks():
    report, site, rt = seo.run_demo(budget=BudgetEnvelope(max_total_cost=3.5))
    st = report.task_states
    assert st["seo-audit"] == "DONE" and st["seo-fix-titles"] == "DONE"      # 1 + 2 = 3
    assert st["seo-fix-meta"] == "BUDGET_EXCEEDED"                            # 3 + 2 > 3.5
    assert report.budget["spent"] == 3.0


# ---- анти-самоотчёт ---------------------------------------------------------------
def test_dishonest_executor_is_caught_by_fresh_verification():
    report, site, rt = seo.run_demo(honest=False)
    st = report.task_states
    assert st["seo-audit"] == "DONE"
    assert st["seo-fix-titles"] == st["seo-fix-meta"] == st["seo-fix-alt"] == "FAILED"
    assert st["seo-rescore"] == "SKIPPED" and st["seo-publish"] == "SKIPPED"
    assert report.status == "FAILED" and not report.verified
    assert site.writes == 0 and site.published is False
    assert report.kpi_before["seo_readiness"] == report.kpi_after["seo_readiness"] == 62.5
    o = {x.task_id: x for x in report.outcomes}["seo-fix-titles"]
    assert o.result.ok is True and o.verification.status == "FAILED"
    assert "still missing" in o.verification.reason
    # перепланирование дало один повтор (max_attempts=2), затем сдалось
    assert report.rounds == 2 and o.attempts == 2
    recs = {r["task_id"]: r for r in report.learning_records}
    assert recs["obj-synthetic-seo/seo-fix-titles"]["learning_status"] == "UNVERIFIED"
    assert recs["obj-synthetic-seo/run"]["learning_status"] != "VERIFIED"
    assert all(learning_validate(r) == [] for r in report.learning_records)


def test_no_verifier_means_unverified_not_verified():
    site = seo.default_site()
    rt = CompanyRuntime(seo.build_plan(), executor=seo.make_executor(site), verifier=None,
                        kpi_reader=seo.make_kpi_reader(site), synthetic=True)
    report = rt.run()
    assert report.task_states["seo-fix-titles"] == "DONE"
    assert report.status == "UNVERIFIED"
    assert all(r["learning_status"] != "VERIFIED" for r in report.learning_records)


def test_verifier_failure_overrides_ok_even_when_verifier_lies_about_shape():
    site = seo.default_site()
    rt = CompanyRuntime(seo.build_plan(), executor=seo.make_executor(site), verifier=lambda t, r: "VERIFIED",
                        kpi_reader=seo.make_kpi_reader(site), synthetic=True)
    assert rt.run().status == "UNVERIFIED"


def test_executor_result_for_other_task_is_failure():
    site = seo.default_site()
    rt = CompanyRuntime(seo.build_plan(), executor=lambda t: WorkResult("someone-else", True),
                        verifier=seo.make_verifier(site), synthetic=True)
    report = rt.run()
    assert report.task_states["seo-audit"] == "FAILED" and report.status == "FAILED"


def test_verification_outcome_rejects_unknown_status():
    with pytest.raises(ValueError):
        VerificationOutcome("PASS")
    assert EvidenceRequirement("site", "score", {"observed": True}).expect["observed"] is True
