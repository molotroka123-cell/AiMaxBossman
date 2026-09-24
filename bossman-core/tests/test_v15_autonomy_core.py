from __future__ import annotations

from pathlib import Path

import pytest

from bossman.resource_brain import ResourceSnapshot
from bossman_v3.autonomy_kernel import BossmanAutonomyKernel
from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.operating_graph import PersonalOperatingGraph
from bossman_v3.resource_manager import (
    AutonomousResourceManager, RouteCandidate, RoutePolicy,
)
from bossman_v3.self_improvement.lab import BenchmarkResult
from bossman_v3.self_improvement.scientist import (
    ExperimentEvidence, Hypothesis, ScientificSelfImprovement,
)
from bossman_v3.skill_factory.compiler import SkillCompiler
from bossman_v3.skill_factory.factory import TraceStep
from bossman_v3.society import PersistentAgentSociety


def result(*, success=0.9, quality=0.9, cost=1.0, latency=10.0,
           tokens=100.0, ram=10.0, vram=0.0, retries=0.0, security=0):
    return BenchmarkResult(success, quality, cost, latency, tokens, ram, vram, retries, security)


def test_persistent_society_learns_role_quality_and_survives_restart(tmp_path):
    path = tmp_path / "society.json"
    s = PersistentAgentSociety(path)
    for _ in range(8):
        s.record_outcome("coder", "coding_bugfix", verified_success=True, latency_s=1.0)
    for _ in range(4):
        s.record_outcome("researcher", "coding_bugfix", verified_success=False, verifier_rejected=True)
    team = s.select_team("coding_bugfix", max_members=2)
    assert team[0] == "coder"
    s.remember_skill("coder", "skill:verified-debug-v3")
    s2 = PersistentAgentSociety(path)
    assert "skill:verified-debug-v3" in s2.roles["coder"].skill_refs
    assert s2.roles["coder"].stats["coding_bugfix"].attempts == 8


def test_operating_graph_keeps_temporal_history_and_provenance(tmp_path):
    g = PersonalOperatingGraph(tmp_path / "graph.json")
    task = g.upsert_node("task", "T-1", source_ref="run:1")
    old = g.upsert_node("branch", "old", source_ref="git:a")
    new = g.upsert_node("branch", "new", source_ref="git:b")
    e1 = g.relate(task, "implemented_on", old, valid_from=10, source_ref="git:a",
                  supersede_relation=True)
    g.relate(task, "implemented_on", new, valid_from=20, source_ref="git:b",
             supersede_relation=True)
    assert g.edges[e1].valid_to == 20
    at15 = g.neighbors(task, relation="implemented_on", as_of=15)
    at25 = g.neighbors(task, relation="implemented_on", as_of=25)
    assert at15[0]["target"]["key"] == "old"
    assert at25[0]["target"]["key"] == "new"
    assert g.edges[e1].source_ref == "git:a"


def test_resource_manager_selects_cheapest_capable_and_blocks_unknown_cloud_price():
    mgr = AutonomousResourceManager()
    policy = RoutePolicy(min_quality_lcb=0.75, max_cost_usd=0.20)
    snap = ResourceSnapshot(1000, 800, 10000, 9000)
    rows = [
        RouteCandidate("local-fast", .80, 0.0, 250, 0.02, True, ram_estimate=100),
        RouteCandidate("cloud-cheap", .90, 0.05, 150, None, False, ram_estimate=0),
        RouteCandidate("cloud-unknown", .99, None, 50, None, False),
        RouteCandidate("too-big", .99, 0.0, 10, 0.1, True, ram_estimate=900),
    ]
    d = mgr.choose(rows, policy, snapshot=snap)
    assert d.candidate_id in {"local-fast", "cloud-cheap"}
    assert all(x["id"] != "cloud-unknown" or not x["eligible"] for x in d.considered)
    assert all(x["id"] != "too-big" or not x["eligible"] for x in d.considered)


def test_skill_compiler_requires_verified_trace_and_unseen_transfer():
    action = TypedAction("file.write", {"path": "x", "content": "ok"},
                         side_effect=SideEffectClass.IDEMPOTENT_WRITE)
    trace = [TraceStep(action=action, verified=True)]
    compiler = SkillCompiler()
    with pytest.raises(ValueError, match="unseen transfer"):
        compiler.compile(
            name="debug_once", task_class="coding", trace=trace,
            input_schema={"path": "string"}, output_schema={"artifact": "string"},
            source_sha="a" * 40, verifier_ref="verifier:1", transfer_ref="holdout:1",
            unseen_transfer_passed=False, verifier_independent=True,
        )
    compiled = compiler.compile(
        name="debug_once", task_class="coding", trace=trace,
        input_schema={"path": "string"}, output_schema={"artifact": "string"},
        source_sha="a" * 40, verifier_ref="verifier:1", transfer_ref="holdout:1",
        unseen_transfer_passed=True, verifier_independent=True,
        trigger_terms=["bug", "regression"],
    )
    assert compiled.candidate.stage.value == "EXPERIMENTAL"
    assert compiled.manifest()["workflow_fingerprint"]


def test_scientific_cycle_promotes_only_measured_verified_pareto_gain():
    sci = ScientificSelfImprovement()
    h = Hypothesis("h1", "selector is at least 40% faster", "latency", .40, "bench:selector")
    base = result(latency=100, cost=1.0)
    cand = result(latency=50, cost=1.0)
    ev = ExperimentEvidence(
        baseline=base, candidate=cand, verifier_passed=True, regression_passed=True,
        unseen_transfer_passed=True, security_non_regression=True,
        source_sha="b" * 40, candidate_ref="evo/candidate-1",
    )
    d = sci.decide(h, ev)
    assert d.promote and d.relative_gain == pytest.approx(.5)
    rejected = sci.decide(h, ExperimentEvidence(
        baseline=base, candidate=cand, verifier_passed=True, regression_passed=True,
        unseen_transfer_passed=False, security_non_regression=True,
        source_sha="b" * 40, candidate_ref="evo/candidate-1",
    ))
    assert not rejected.promote


def test_autonomy_kernel_binds_society_graph_and_resource_route(tmp_path):
    k = BossmanAutonomyKernel(tmp_path)
    k.society.remember_skill("coder", "skill:test-driven-debug")
    project = k.graph.upsert_node("project", "bossman")
    plan = k.plan(
        task_id="T-42", task_class="coding_bugfix",
        route_candidates=[
            RouteCandidate("local", .82, 0, 200, .02, True),
            RouteCandidate("cloud", .95, .10, 100, None, False),
        ],
        route_policy=RoutePolicy(min_quality_lcb=.8),
        graph_refs=[("project", "bossman")],
        required_roles=("verifier",), max_team=3,
    )
    assert "verifier" in plan.team
    assert plan.route.candidate_id in {"local", "cloud"}
    assert "skill:test-driven-debug" in plan.skill_refs or "coder" not in plan.team
    assert plan.graph_context["nodes"][0]["id"] == project
    k.record(plan, verified_success=True, branch="feature/x", benchmark_ref="bench:1",
             source_ref="run:42")
    assert k.graph.context([("task", "T-42")])["nodes"]
