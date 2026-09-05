"""V3_ORG_FLEET_STRESS — пять обязательных детерминированных стресс-бенчмарков над
РЕАЛЬНЫМИ Organization/Fleet/CompoundRunner (не над заглушками):
A CompoundFailureBenchmark, B CrossDepartmentLeakProbe, C LongHorizonResumeBenchmark,
D FleetTopologyStress, E TokenValueMetric. Бенчмарк пассивен: судит по durable-истине."""
from __future__ import annotations

import pytest

from bossman_v3.benchmark_overlay import (BenchmarkCollector, BenchmarkScorer, OrgBenchmarkSuite,
                                          events_from_organization, events_from_task_journal)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.contracts import TypedAction
from bossman_v3.fleet import CLOUD, FleetScheduler, NodeState, PlacementRequirement
from bossman_v3.memory.journal import TaskJournal
from bossman_v3.organization import (EXECUTOR, REVIEWER, AgentProfile, DelegationContract, Department,
                                     EscalationPolicy, EvidenceRequirement, ExportBlocked, MissionState, Resources,
                                     RiskTier, TaskState)
from test_v3_compound_resume import _Executor, _agent
from test_v3_organization_e2e import Org, _contract, _write_step

SUITE = OrgBenchmarkSuite()


@pytest.fixture
def org(tmp_path) -> Org:
    o = Org(tmp_path)
    rt = o.runtime
    rt.set_organization_budget(Resources(usd=100))
    rt.register_department(Department("engineering", capabilities={"fs.write"}, budget=Resources(usd=10, tokens=100_000, compute_seconds=3600)))
    rt.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm"))
    rt.register_agent(AgentProfile("coder2", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_strong", model="qwen"))
    rt.register_agent(AgentProfile("rev", "engineering", {REVIEWER}, {"fs.write"}, tier="local_small", model="llama"))
    return o


# A ---------------------------------------------------------------------------
def test_compound_failure_benchmark_parent_cannot_complete_with_one_unverified_child(org):
    rt, w = org.runtime, org.world
    contracts = [_contract(w, f"w{i}", [f"f{i}.txt"], risk=RiskTier.LOW) for i in range(1, 7)]      # 6 обязательных детей
    # w4 требует улику f4.txt, а его шаг пишет другой файл → эффект есть, требуемого нет → не VERIFIED
    contracts[3] = DelegationContract(
        work_id="w4", mission_id="m1", department_id="engineering", goal="создать f4", required_capability="fs.write",
        success_criteria=["f4 существует"], evidence_required=[EvidenceRequirement("file", str(w.root / "f4.txt"))],
        budget=Resources(usd=0.5, tokens=1000, compute_seconds=60), risk=RiskTier.LOW,
        escalation=EscalationPolicy(max_attempts=2, on_failure="escalate_tier"),
        steps=[_write_step(w, "w4-s1", "not-f4.txt")])
    rt.receive_mission("m1", title="compound", department_id="engineering", contracts=contracts)
    status = rt.run_mission("m1")
    for _ in range(3):
        if status.done or rt.store.work("w4")["state"] == TaskState.FAILED.value:
            break
        status = rt.run_mission("m1")

    def scenario():
        works = rt.store.works("m1")
        return {"children": [{"work_id": x["work_id"], "verified": x["state"] == TaskState.COMPLETED.value,
                              "failed": x["state"] == TaskState.FAILED.value} for x in works],
                "parent_completed": rt.store.mission("m1")["state"] == MissionState.COMPLETED.value}
    res = SUITE.compound_failure(scenario)
    assert res.passed, res
    assert not status.done and rt.store.mission("m1")["state"] != MissionState.COMPLETED.value
    assert sum(1 for x in rt.store.works("m1") if x["state"] == TaskState.COMPLETED.value) == 5
    # пассивный бенчмарк над durable-истиной: ни false_success, ни parent_success_with_failed_child
    col = BenchmarkCollector(); col.extend(events_from_organization(rt.store, "m1"))
    rep = BenchmarkScorer().score_report("v3", "t", "DETERMINISTIC", col.by_mission())
    assert rep.aggregate["hard_failures"] == [] and rep.aggregate["verified_success_count"] == 0


# B ---------------------------------------------------------------------------
def test_cross_department_leak_probe(org):
    rt = org.runtime
    trading = Department("trading", capabilities={"fs.write"}, budget=Resources(usd=1), allowed_exports={"summary"})
    rt.register_department(trading)
    rt.register_department(Department("research", capabilities={"fs.write"}, budget=Resources(usd=1)))
    secret = rt.knowledge.publish("department:trading", "position", {"ticker": "XYZ", "size": 1_000_000},
                                  provenance="trading-desk", confidence=0.9)

    def probe():
        visible = rt.knowledge.read("department:research")
        leaked = any("XYZ" in str(f.payload) for f in visible)
        blocked = False
        try:
            rt.knowledge.export(secret, to_scope="department:research", source_department=trading)
        except ExportBlocked:
            blocked = True
        after = rt.knowledge.read("department:research")
        return {"blocked": blocked, "leaked": leaked or any("XYZ" in str(f.payload) for f in after),
                "visible_before": len(visible), "visible_after": len(after)}
    res = SUITE.cross_department_leak(probe)
    assert res.passed, res
    assert res.metrics["visible_after"] == 0


# C ---------------------------------------------------------------------------
def test_long_horizon_resume_benchmark_crash_at_step_7(tmp_path):
    plan = [PlanStep(f"s{i}", f"шаг {i}", TypedAction("proj.step", {"step_id": f"s{i}"})) for i in range(1, 11)]
    journal = TaskJournal.start(task_id="long", plan=[(s.step_id, s.intent) for s in plan], root=tmp_path / "j")

    def scenario():
        dying = _Executor(boom_on={"s7"})
        first = CompoundRunner(_agent(dying), journal).run(plan)
        assert first.completed is False and dying.seen == [f"s{i}" for i in range(1, 7)]
        revived = TaskJournal.load(task_id="long", root=tmp_path / "j")
        healthy = _Executor()
        second = CompoundRunner(_agent(healthy), revived).run(plan)
        replayed = [s for s in healthy.seen if s in dying.seen]
        events = events_from_task_journal(revived, "long", replayed_steps=replayed)
        dup = BenchmarkScorer().gate.evaluate(events).count("duplicate_side_effect")
        return {"total_steps": 10, "crash_after_step": 6 if False else 7, "resumed": second.completed,
                "completed_steps": len(revived.finished()), "replayed_steps": replayed,
                "duplicate_side_effect_count": dup, "second_run_steps": healthy.seen}
    res = SUITE.long_horizon_resume(scenario)
    assert res.passed, res
    assert res.metrics["second_run_steps"] == [f"s{i}" for i in range(7, 11)]
    assert res.metrics["resume_sla_claimable"] is False       # SLA только по измерению на интегрированном рантайме


# D ---------------------------------------------------------------------------
def _node(nid, *, ram, gpu, privacy="private", trust="trusted_local", unified=False, caps=("fs.write",)):
    return NodeState(nid, hostname=nid, os_name="Linux", ram_gb=ram, gpu_memory_gb=gpu, unified_memory=unified,
                     capabilities=set(caps), privacy_level=privacy, trust_class=trust, last_heartbeat_ts=1.0)


def test_fleet_topology_stress_laptop_aimax_cloud():
    laptop = _node("laptop-8gb", ram=8, gpu=0)
    aimax = _node("ai-max-128", ram=128, gpu=96, unified=True, caps=("fs.write", "vision.large"))
    cloud = _node("cloud-a100", ram=256, gpu=80, privacy="public", trust=CLOUD)
    nodes = [laptop, cloud, aimax]
    s = FleetScheduler()

    def scenario():
        big_private, _ = s.choose(nodes, PlacementRequirement(capabilities=("fs.write",), min_gpu_memory_gb=48, privacy="private"))
        small_private, _ = s.choose(nodes, PlacementRequirement(capabilities=("fs.write",), min_ram_gb=4, privacy="private"))
        vision, _ = s.choose(nodes, PlacementRequirement(capabilities=("vision.large",), privacy="private"))
        huge_private, ex = s.choose(nodes, PlacementRequirement(capabilities=("fs.write",), min_ram_gb=200, privacy="private"))
        huge_public, _ = s.choose(nodes, PlacementRequirement(capabilities=("fs.write",), min_ram_gb=200, privacy="public"))
        chosen = [x.node_id for x in (big_private, small_private, vision) if x is not None]
        return {
            "capability_respected": vision is not None and vision.node_id == "ai-max-128",
            "memory_respected": big_private is not None and big_private.node_id == "ai-max-128"
            and small_private is not None and small_private.node_id != "cloud-a100",
            "privacy_respected": huge_private is None and "cloud-a100" not in chosen,
            "privacy_violation": any(n == "cloud-a100" for n in chosen),
            "public_big_goes_to_cloud": huge_public is not None and huge_public.node_id == "cloud-a100",
            "blocked_reason": FleetScheduler.admission_reason(ex), "rejections": len(ex),
            "no_manual_ip_routing": not any(k in {"ip", "host", "port"} for n in nodes for k in n.labels),
        }
    res = SUITE.fleet_topology_stress(scenario)
    assert res.passed, res
    assert res.metrics["public_big_goes_to_cloud"] and res.metrics["rejections"] == 3


# E ---------------------------------------------------------------------------
def test_token_value_metric_na_for_zero_cost():
    assert SUITE.token_value_metric(quality=0.9, reliability=1.0, cost=0.0) is None
    assert SUITE.token_value_metric(quality=0.9, reliability=0.5, cost=1.5) == 0.3
    assert BenchmarkScorer.token_value_metric(quality=1.0, reliability=1.0, cost=4.0) == 0.25
