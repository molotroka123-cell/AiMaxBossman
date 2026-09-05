"""Organization Layer — единицы: контракты/улики, рынок способностей и
эскалация, команды по риску, казначейство, обучение, скоупы памяти, события,
durable store.

Ни одного фейкового «verified=True» здесь не принимается за доказательство:
тесты специально подсовывают улики с verified=True из недоверенного источника
и ожидают отказ — доверие есть свойство слоя, а не флага.
"""
from __future__ import annotations

import pytest

from bossman_v3.organization import (
    EXECUTOR, LEAD, REVIEWER, RISK, AgentProfile, CapabilityMarketplace, DelegationContract, Department,
    EscalationPolicy, EventIntake, Evidence, EvidenceRequirement, ExportBlocked, OrganizationStore,
    OrganizationalLearning, Reaction, Resources, ResourceTreasury, RiskTier, ScopedKnowledge, TaskState,
    WorkResult, consensus, event_key, required_roles)
from bossman_v3.organization.teams import AdaptiveTeamFormer


def _contract(**kw) -> DelegationContract:
    base = dict(work_id="w1", mission_id="m1", department_id="engineering", goal="создать файл",
                required_capability="fs.write", success_criteria=["файл существует"],
                evidence_required=[EvidenceRequirement("file", "/tmp/x")],
                budget=Resources(usd=1.0, tokens=1000, compute_seconds=60))
    base.update(kw)
    return DelegationContract(**base)


def _agent(aid, *, tier="local_small", roles=(EXECUTOR,), caps=("fs.write",), dept="engineering", **kw):
    return AgentProfile(aid, dept, set(roles), set(caps), tier=tier, **kw)


# --------------------------------------------------------------- contracts

def test_prose_is_not_evidence_even_with_verified_flag_from_untrusted_source():
    c = _contract()
    r = WorkResult("w1", executed=True, claims={"done": True, "summary": "я всё сделал"},
                   evidence=[Evidence("file", "/tmp/x", verified=True, source="model:self-report")])
    ok, errors = c.validate(r)
    assert ok is False
    assert any("untrusted source" in e for e in errors)


def test_journal_backed_evidence_is_accepted():
    c = _contract()
    r = WorkResult("w1", executed=True, evidence=[Evidence("file", "/tmp/x", True, source="journal:m1__w1/s1")])
    ok, errors = c.validate(r)
    assert ok and errors == []


def test_missing_and_unverified_evidence_fail_closed():
    c = _contract()
    ok, errors = c.validate(WorkResult("w1", executed=True, evidence=[]))
    assert not ok and "missing evidence:file@/tmp/x" in errors
    ok, errors = c.validate(WorkResult("w1", executed=True,
                                       evidence=[Evidence("file", "/tmp/x", False, source="journal:m1__w1/s1")]))
    assert not ok and "unverified evidence:file" in errors


def test_side_effect_work_with_nothing_executed_is_not_success():
    c = _contract()
    ok, errors = c.validate(WorkResult("w1", executed=False,
                                       evidence=[Evidence("file", "/tmp/x", True, source="journal:m1__w1/s1")]))
    assert not ok and "nothing was executed" in errors[0]


def test_contract_problems_require_evidence_for_side_effects_but_not_for_informational():
    assert "side-effect work must declare evidence_required" in _contract(evidence_required=[]).problems()
    assert _contract(evidence_required=[], side_effect=False).problems() == []
    assert "goal is empty" in _contract(goal="  ").problems()


def test_contract_digest_is_stable_across_roundtrip_and_ignores_runtime_metadata():
    c = _contract()
    d = c.digest()
    c.metadata["runtime"] = {"last_reason": "x"}
    assert DelegationContract.from_dict(c.to_dict()).digest() == d


def test_consensus_counts_only_verified_results():
    good = WorkResult("w", True, [Evidence("t", "r", True, source="journal:a/b")], success=True)
    claimed = WorkResult("w", True, [Evidence("t", "r", False, source="journal:a/b")], success=True)
    assert consensus([good, good]) is True
    assert consensus([good, claimed]) is False


# -------------------------------------------------------------- marketplace

def test_marketplace_prefers_cheaper_tier_over_lower_load():
    m = CapabilityMarketplace([_agent("cloud", tier="cheap_cloud"),
                               _agent("local", tier="local_small", current_load=1)])
    assert m.route(_contract()).selected == ("local",)


def test_marketplace_requests_capability_not_model_and_respects_department():
    m = CapabilityMarketplace([_agent("r", dept="research"), _agent("e", caps=("browser.open",))])
    d = m.route(_contract())
    assert d.selected == () and d.requires_owner
    assert "department" in d.rejected["r"] and "capability" in d.rejected["e"]


def test_marketplace_false_success_history_demotes_agent():
    learning = OrganizationalLearning()
    for _ in range(3):
        learning.observe("liar", "fs.write", verified=False, claimed_success=True)
    learning.observe("honest", "fs.write", verified=True, claimed_success=True)
    m = CapabilityMarketplace([_agent("liar"), _agent("honest")], learning)
    assert m.route(_contract()).selected == ("honest",)


def test_escalation_goes_exactly_one_tier_up_after_failure():
    m = CapabilityMarketplace([_agent("a", tier="local_small"), _agent("b", tier="local_strong"),
                               _agent("c", tier="frontier")])
    assert m.escalated_min_tier(_contract(), failed_agents=["a"]) == "local_strong"
    d = m.route(_contract(), min_tier="local_strong", exclude={"a"})
    assert d.selected == ("b",)          # frontier не получает механическую работу


def test_reviewer_must_be_independent_of_producer_including_same_model_alias():
    m = CapabilityMarketplace([
        _agent("coder", model="qwen-14b"),
        _agent("coder-alias", roles=(REVIEWER,), model="Qwen-14B"),
        _agent("other", roles=(REVIEWER,), model="claude"),
    ])
    d = m.route_reviewer(_contract(), producer_id="coder")
    assert d.selected == ("other",)
    assert "excluded" in d.rejected["coder-alias"]


def test_risk_clearance_gates_high_risk_work():
    m = CapabilityMarketplace([_agent("junior", risk_clearance=RiskTier.LOW)])
    assert m.route(_contract(risk=RiskTier.HIGH)).selected == ()
    assert m.route(_contract(risk=RiskTier.LOW)).selected == ("junior",)


# -------------------------------------------------------------------- teams

def test_team_size_is_proportional_to_risk():
    d = Department("engineering")
    assert required_roles(RiskTier.LOW, d) == [EXECUTOR]
    assert required_roles(RiskTier.MEDIUM, d) == [EXECUTOR, REVIEWER]
    assert required_roles(RiskTier.HIGH, d) == [LEAD, EXECUTOR, REVIEWER]
    assert required_roles(RiskTier.HIGH, Department("trading", require_risk_review=True)) == [LEAD, EXECUTOR, REVIEWER, RISK]


def test_team_former_builds_graph_and_reports_unfilled_roles():
    hi = dict(risk_clearance=RiskTier.HIGH)
    m = CapabilityMarketplace([_agent("lead", roles=(LEAD,), **hi), _agent("coder", **hi), _agent("rev", roles=(REVIEWER,), **hi)])
    former = AdaptiveTeamFormer(m)
    t = former.form(team_id="t1", mission_id="m1", department=Department("engineering"),
                    contract=_contract(risk=RiskTier.HIGH))
    assert t.slots == {"lead": "lead", "executor": "coder", "reviewer": "rev"}
    kinds = {(e["from"], e["to"], e["kind"]) for e in t.edges}
    assert ("lead", "coder", "delegation") in kinds and ("rev", "coder", "review") in kinds
    assert ("lead", "w1", "ownership") in kinds
    solo = former.form(team_id="t2", mission_id="m1", department=Department("engineering"),
                       contract=_contract(risk=RiskTier.MEDIUM), exclude={"rev"})
    solo2 = AdaptiveTeamFormer(CapabilityMarketplace([_agent("coder")])).form(
        team_id="t3", mission_id="m1", department=Department("engineering"), contract=_contract(risk=RiskTier.MEDIUM))
    assert solo2.unfilled == ["reviewer"] and not solo2.complete
    assert solo.complete


# ----------------------------------------------------------------- treasury

def test_treasury_checks_every_envelope_and_names_the_blocking_one():
    t = ResourceTreasury()
    t.set_limit("organization", Resources(usd=100))
    t.set_limit("department:eng", Resources(usd=1.0))
    scopes = ["organization", "department:eng", "mission:m1"]
    d = t.reserve(scopes, Resources(usd=0.8))
    assert d.allowed
    d2 = t.preflight(scopes, Resources(usd=0.5))
    assert not d2.allowed and d2.scope == "department:eng" and d2.ask_owner


def test_treasury_reserve_commit_release_and_overrun_is_recorded_not_hidden():
    t = ResourceTreasury()
    t.set_limit("mission:m1", Resources(usd=1.0))
    t.reserve(["mission:m1"], Resources(usd=0.5))
    assert t.envelope("mission:m1").reserved.usd == 0.5
    d = t.commit(["mission:m1"], Resources(usd=0.5), Resources(usd=1.5))
    env = t.envelope("mission:m1")
    assert env.reserved.usd == 0 and env.spent.usd == 1.5
    assert not d.allowed and "overrun" in d.reason
    t.reserve(["mission:m1"], Resources(usd=0.1))          # мимо — но резерв не пройдёт
    t.release(["mission:m1"], Resources(usd=0.1))
    assert t.envelope("mission:m1").reserved.usd == 0


# ----------------------------------------------------------------- learning

def test_learning_is_conservative_and_decays(tmp_path):
    store = OrganizationStore(tmp_path / "org.sqlite")
    L = OrganizationalLearning(store)
    assert L.stats("x", "cap").reliability == 0.5             # неизвестно ≠ надёжно
    for _ in range(5):
        L.observe("x", "cap", verified=False, claimed_success=True)
    low = L.stats("x", "cap").reliability
    for _ in range(10):
        L.observe("x", "cap", verified=True, claimed_success=True)
    assert L.stats("x", "cap").reliability > low + 0.3      # забывание даёт подняться
    assert OrganizationalLearning(store).stats("x", "cap").attempts == pytest.approx(L.stats("x", "cap").attempts)
    assert L.failing_agents() == []


# ------------------------------------------------------------- memory scope

def test_scoped_knowledge_isolates_projects_and_exports_only_allowlisted_kinds(tmp_path):
    store = OrganizationStore(tmp_path / "org.sqlite")
    k = ScopedKnowledge(store)
    k.publish("project:A", "verified_fact", {"x": 1}, provenance="journal:a/s1")
    k.publish("project:A", "raw_state", {"token": "sk-abcdefghijklmnopqrstuvwxyz0123"},  # ci-secret-scan: allow — канарейка
              provenance="dump")
    assert k.read("project:B") == []
    assert [f.kind for f in k.read("project:A")] == ["verified_fact", "raw_state"]
    raw = k.read("project:A", kind="raw_state")[0]
    assert "sk-abc" not in str(raw.payload) and "[REDACTED]" in str(raw.payload)   # секрет отредактирован до записи
    eng = Department("engineering")
    fact = k.read("project:A", kind="verified_fact")[0]
    exported = k.export(fact, to_scope="project:B", source_department=eng)
    assert exported.source_scope == "project:A" and "exported from project:A" in exported.provenance
    with pytest.raises(ExportBlocked):
        k.export(raw, to_scope="project:B", source_department=eng)


def test_failure_memory_is_per_department(tmp_path):
    store = OrganizationStore(tmp_path / "org.sqlite")
    k = ScopedKnowledge(store, failure_root=tmp_path / "failures")
    k.failure_memory("engineering").record({"signature": "s", "error": "boom"})
    assert k.failure_memory("trading").query("s") == []
    assert len(k.failure_memory("engineering").query("s")) == 1


# ------------------------------------------------------------------- events

def _reaction():
    return Reaction("ci.failed", "engineering", "ci.triage", "триаж CI: {job}",
                    evidence=(EvidenceRequirement("file", "triage.md"),), max_open_per_kind=1)


def test_events_dedup_and_backpressure_and_unknown_kinds(tmp_path):
    store = OrganizationStore(tmp_path / "org.sqlite")
    intake = EventIntake(store, [_reaction()])
    out, contract = intake.accept("ci.failed", {"job": "tests", "delivery_id": "1"}, mission_id="events")
    assert out.accepted and contract is not None and contract.goal == "триаж CI: tests"
    store.save_work(contract, state=TaskState.PLANNED)
    dup, _ = intake.accept("ci.failed", {"job": "tests", "delivery_id": "2"}, mission_id="events")
    assert not dup.accepted and dup.duplicate
    bp, _ = intake.accept("ci.failed", {"job": "lint"}, mission_id="events")
    assert not bp.accepted and "backpressure" in bp.reason
    unknown, _ = intake.accept("deploy.now", {"target": "prod"}, mission_id="events")
    assert not unknown.accepted and store.event(event_key("deploy.now", {"target": "prod"}))["outcome"] == "rejected:no_reaction"


def test_event_reaction_is_a_contract_not_a_side_effect(tmp_path):
    store = OrganizationStore(tmp_path / "org.sqlite")
    _, contract = EventIntake(store, [_reaction()]).accept("ci.failed", {"job": "x"}, mission_id="events")
    assert contract.side_effect and contract.evidence_required            # обычный контракт с уликами
    assert contract.escalation == EscalationPolicy(max_attempts=2, on_failure="fail")
    assert contract.problems() == []


# -------------------------------------------------------------------- store

def test_store_roundtrips_everything_needed_for_restart(tmp_path):
    store = OrganizationStore(tmp_path / "org.sqlite")
    d = Department("engineering", purpose="код", capabilities={"fs.write"}, require_reviewer=True)
    store.save_department(d)
    store.save_agent(_agent("coder", model="glm"))
    c = _contract()
    store.save_work(c, state=TaskState.EXECUTING, assigned=["coder"], attempts=1)
    r = WorkResult("w1", True, [Evidence("file", "/tmp/x", True, source="journal:m1__w1/s1")], success=True)
    store.save_result(r, "m1")
    store.save_envelope("department:engineering", limit=Resources(usd=5), spent=Resources(usd=1))

    again = OrganizationStore(tmp_path / "org.sqlite")
    assert again.departments()[0].to_dict() == d.to_dict()
    assert again.agents()[0].model == "glm"
    w = again.work("w1")
    assert w["state"] == "executing" and w["assigned"] == ["coder"] and w["attempts"] == 1
    assert w["contract"].to_dict() == c.to_dict()
    assert again.result("w1").verified is True
    assert again.envelopes()["department:engineering"][1].usd == 1


def test_store_refuses_in_memory_database(tmp_path):
    with pytest.raises(ValueError):
        OrganizationStore(":memory:")
