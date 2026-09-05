"""Закрытие P1 из внешнего Astra-аудита (docs/audits/astra-7b1377a), в объёме execution truth:
ASTRA-002 возобновление только по подписанным шагам; ASTRA-003 идентичность плана = действия,
не только id; ASTRA-004 улика чужой работы не подтверждает эту; O001 private ⇒ нет cloud-агентов;
O002 коллизия work_id между миссиями; O003 отрицательные/NaN ресурсы не проходят admission."""
from __future__ import annotations

import json
import math

import pytest

from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory.journal import JournalIntegrityError, TaskJournal
from bossman_v3.organization import (EXECUTOR, AgentProfile, DelegationContract, Department, Evidence,
                                     EvidenceRequirement, Resources, RiskTier, TaskState, WorkResult)
from bossman_v3.organization.marketplace import CapabilityMarketplace
from bossman_v3.organization.learning import OrganizationalLearning
from test_v3_compound_resume import _Executor, _agent, _plan
from test_v3_organization_e2e import Org, _contract, _write_step


def test_astra_002_unsigned_finished_flags_do_not_skip_work(tmp_path):
    j = TaskJournal.start(task_id="chain", plan=[(s.step_id, s.intent) for s in _plan()], root=tmp_path)
    # злоумышленник/битый файл: s1 и s2 «закрыты» флагами, но без подписи
    path = tmp_path / "chain.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for st in raw["steps"][:2]:
        st.update(receipt={"effect_id": "forged"}, verified=True, status="DONE", updated_at="2026-01-01T00:00:00+00:00")
    path.write_text(json.dumps(raw), encoding="utf-8")
    # SALVAGE-004: журнал с «закрытыми», но неподписанными шагами не загружается вовсе —
    # подделка блокирует resume (JournalIntegrityError), а не «исполняем заново молча».
    with pytest.raises(JournalIntegrityError, match="unsigned completion"):
        TaskJournal.load(task_id="chain", root=tmp_path)
    # честный журнал того же плана: подписанные закрытия — единственный способ пропустить шаг
    ex = _Executor()
    clean = TaskJournal.start(task_id="chain2", plan=[(s.step_id, s.intent) for s in _plan()], root=tmp_path)
    res = CompoundRunner(_agent(ex), clean).run(_plan())
    assert res.completed and ex.seen[:2] == ["s1", "s2"]
    fixed = TaskJournal.load(task_id="chain2", root=tmp_path)
    assert len(fixed.finished_signed()) == 5 and fixed.steps[0].receipt.get("effect_id") != "forged"


def test_astra_003_changed_plan_under_same_ids_is_blocked_not_resumed(tmp_path):
    org = Org(tmp_path); rt = org.runtime
    rt.set_organization_budget(Resources(usd=100))
    rt.register_department(Department("engineering", capabilities={"fs.write"}, budget=Resources(usd=10, tokens=100_000, compute_seconds=3600)))
    rt.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm"))
    w = org.world
    c = _contract(w, "w1", ["a.txt", "b.txt"], risk=RiskTier.LOW)
    org.world.ask_for.add("b.txt")                                  # второй шаг ждёт владельца → журнал с закрытым s1
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[c])
    st = rt.run_mission("m1")
    assert st.waiting_approval == ("w1",) and w.side_effects() == 1
    # план меняется: те же id шагов, другое действие → не «продолжить», а BLOCKED владельцу
    work = rt.store.work("w1")["contract"]
    work.steps = [_write_step(w, "w1-s1", "evil.txt"), _write_step(w, "w1-s2", "b.txt")]
    # SALVAGE-004: контракт неизменяем — подмена шагов под тем же work_id отвергается хранилищем
    with pytest.raises(ValueError, match="immutable work contract changed"):
        rt.store.save_work(work, state=TaskState.PLANNED, attempts=0)
    org.world.approved.add("b.txt")
    st = rt.run_mission("m1")
    assert w.side_effects() <= 2 and not (w.root / "evil.txt").exists()


def test_astra_004_evidence_from_another_work_does_not_satisfy_contract():
    c = DelegationContract(work_id="w1", mission_id="m1", department_id="engineering", goal="создать файл",
                           required_capability="fs.write", success_criteria=["ок"],
                           evidence_required=[EvidenceRequirement("file", "/tmp/x")],
                           budget=Resources(usd=1.0, tokens=1000, compute_seconds=60))
    foreign = Evidence.signed("file", "/tmp/x", source="journal:m9__w9/s1")     # валидная подпись, чужая работа
    ok, errors = c.validate(WorkResult("w1", executed=True, evidence=[foreign]))
    assert not ok and errors                      # чужая работа/миссия: подпись валидна, но улика не связана с w1


def test_o001_private_work_never_routes_to_cloud_tier_agents():
    learning = OrganizationalLearning(None)
    agents = [AgentProfile("cloud", "engineering", {EXECUTOR}, {"fs.write"}, tier="frontier", model="claude"),
              AgentProfile("cheap", "engineering", {EXECUTOR}, {"fs.write"}, tier="cheap_cloud", model="glm-cloud"),
              AgentProfile("local", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm")]
    m = CapabilityMarketplace(agents, learning)
    c = DelegationContract(work_id="w1", mission_id="m1", department_id="engineering", goal="x", required_capability="fs.write",
                           success_criteria=["ок"], evidence_required=[EvidenceRequirement("file", "/x")],
                           budget=Resources(usd=5), privacy="private")
    why = {a.agent_id: m._reject_reason(a, c, EXECUTOR, "deterministic", set()) for a in agents}
    assert why["cloud"] and why["cheap"] and why["local"] == ""
    c_pub = DelegationContract.from_dict({**c.to_dict(), "privacy": "public"})
    assert m._reject_reason(agents[0], c_pub, EXECUTOR, "deterministic", set()) == ""
    routed = m.route(c, role=EXECUTOR)
    assert routed.selected == ("local",) or list(routed.selected) == ["local"]


def test_o002_work_id_collision_across_missions_is_rejected(tmp_path):
    org = Org(tmp_path); rt = org.runtime
    rt.register_department(Department("engineering", capabilities={"fs.write"}, budget=Resources(usd=10)))
    rt.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm"))
    rt.receive_mission("m1", title="a", department_id="engineering", contracts=[_contract(org.world, "w1", ["a.txt"])])
    with pytest.raises(ValueError, match="already belongs to mission"):
        rt.receive_mission("m2", title="b", department_id="engineering", contracts=[_contract(org.world, "w1", ["b.txt"], mission="m2")])


def test_o003_negative_or_non_finite_budget_is_a_contract_problem():
    base = dict(work_id="w1", mission_id="m1", department_id="engineering", goal="x", required_capability="fs.write",
                success_criteria=["ок"], evidence_required=[EvidenceRequirement("file", "/x")])
    assert DelegationContract(**base, budget=Resources(usd=1)).problems() == []
    # SALVAGE-004 (O003): невалидные ресурсы отвергаются уже конструктором Resources
    for bad in (dict(usd=-1), dict(tokens=float("nan")), dict(compute_seconds=math.inf)):
        with pytest.raises(ValueError, match="finite and nonnegative"):
            Resources(**bad)
