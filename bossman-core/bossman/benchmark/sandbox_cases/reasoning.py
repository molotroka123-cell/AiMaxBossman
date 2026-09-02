"""REAL_SANDBOX cases for the reasoning boundary: executable DAG compilation,
adaptive compute-budget selection and evidence-based verification."""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from ..sandbox_row import CaseProbe


def _with_command_center() -> None:
    """`bcc.*` lives in the sibling command-center package, not under bossman-core."""
    root = str(Path(__file__).resolve().parents[4] / "command-center")
    if root not in sys.path:
        sys.path.append(root)


# --------------------------------------------------------------------- DAG
def dag_compiler(seed: int) -> dict:
    probe = CaseProbe("sandbox.dag_compiler", "dag_compiler", seed)
    _with_command_center()
    from bcc.v2.task_graph import (GraphValidationError, TaskGraph, graph_context_view, mark_failed,
                                   mark_running, mark_succeeded, ready_nodes, skip_with_dependents)
    from bossman.company import synthetic_seo as seo
    from bossman.company.model import (AgentRole, BudgetEnvelope, CompanyObjective, CompanyPlan,
                                       CompanyTask, TaskDependency, Workstream)

    spec = [{"node_id": "ingest", "action_type": "shell"},
            {"node_id": "lint", "action_type": "shell", "depends_on": ["ingest"]},
            {"node_id": "test", "action_type": "shell", "depends_on": ["ingest"]},
            {"node_id": "report", "action_type": "shell", "depends_on": ["lint", "test"]},
            {"node_id": "publish", "action_type": "shell", "depends_on": ["report"]}]

    # (1) the scheduling drain bcc.features.missions._compile_plan runs: diamond DAG,
    # two independent branches become ready together, joins wait for both.
    g = TaskGraph.from_list(spec)
    batches: list[list[str]] = []
    while True:
        batch = [n.node_id for n in ready_nodes(g)]
        if not batch:
            break
        batches.append(batch)
        for nid in batch:
            mark_running(g, nid)
            mark_succeeded(g, nid)
    probe.positive("topological_batches_respect_dependencies", batches,
                   [["ingest"], ["lint", "test"], ["report"], ["publish"]])

    # (2) retry / failure / skip cascade on a fresh graph of the same shape
    h = TaskGraph.from_list(spec)
    mark_running(h, "ingest"), mark_succeeded(h, "ingest")
    mark_running(h, "test"), mark_failed(h, "test", "transient")
    after_fail = [h.nodes["test"].status, h.nodes["test"].attempts]
    mark_running(h, "test"), mark_succeeded(h, "test")          # retry budget left -> recovery
    probe.positive("retry_recovers_transient_failure", after_fail + [h.nodes["test"].status],
                   ["PENDING", 1, "SUCCEEDED"])
    for _ in range(3):                                          # retry_limit=2 -> 3rd attempt is fatal
        mark_running(h, "lint"), mark_failed(h, "lint", "broken")
    probe.positive("exhausted_retries_block_dependents",
                   {"lint": h.nodes["lint"].status, "attempts": h.nodes["lint"].attempts,
                    "report": h.nodes["report"].status},
                   {"lint": "FAILED", "attempts": 3, "report": "BLOCKED"})
    probe.negative("blocked_node_never_becomes_ready", [n.node_id for n in ready_nodes(h)], [])
    probe.positive("graph_context_view_stays_local", graph_context_view(h, "report"),
                   {"current_node": "report", "dependencies": {"lint": "FAILED", "test": "SUCCEEDED"},
                    "completed_nodes": ["ingest", "test"], "failed_nodes": ["lint"],
                    "next_ready_nodes": []})
    skip_with_dependents(h, "report")
    probe.positive("skip_cascades_to_transitive_dependents",
                   {k: v.status for k, v in h.nodes.items()},
                   {"ingest": "SUCCEEDED", "lint": "FAILED", "test": "SUCCEEDED",
                    "report": "SKIPPED", "publish": "SKIPPED"})

    # (3) the bossman-core plan compiler (Kahn order + declared edges)
    plan = seo.build_plan()
    probe.positive("company_plan_topological_order", [t.id for t in plan.ordered()],
                   ["seo-audit", "seo-fix-titles", "seo-fix-meta", "seo-fix-alt",
                    "seo-rescore", "seo-publish"])
    probe.positive("company_plan_join_edges", list(plan.dag()["seo-rescore"]),
                   ["seo-fix-titles", "seo-fix-meta", "seo-fix-alt"])

    # (4) hostile / invalid graphs are refused by the real validator
    probe.refused("cycle_refused", lambda: TaskGraph.from_list(
        [{"node_id": "a", "action_type": "shell", "depends_on": ["b"]},
         {"node_id": "b", "action_type": "shell", "depends_on": ["a"]}]),
        GraphValidationError, contains="cycle detected through node")
    probe.refused("unknown_dependency_refused", lambda: TaskGraph.from_list(
        [{"node_id": "a", "action_type": "shell", "depends_on": ["ghost"]}]),
        GraphValidationError, contains="depends on unknown node 'ghost'")
    probe.refused("retry_limit_bomb_refused", lambda: TaskGraph.from_list(
        [{"node_id": "a", "action_type": "shell", "retry_limit": 999}]),
        GraphValidationError, contains="retry_limit must be int in [0, 10]")
    probe.refused("unknown_action_type_refused", lambda: TaskGraph.from_list(
        [{"node_id": "a", "action_type": "rm -rf /"}], known_action_types={"shell"}),
        GraphValidationError, contains="unknown action type 'rm -rf /'")
    running = TaskGraph.from_list([{"node_id": "a", "action_type": "shell"}])
    mark_running(running, "a")
    probe.refused("double_start_refused", lambda: mark_running(running, "a"),
                  GraphValidationError, contains="cannot start from status RUNNING")
    cyclic_plan = CompanyPlan(
        objective=CompanyObjective(id=f"obj-{seed}", title="t", domain="generic"),
        budget=BudgetEnvelope(10), roles=(AgentRole("r", "d"),),
        workstreams=(Workstream("w", "W", "d"),),
        tasks=(CompanyTask("a", "w", "A", "act", "r", dependencies=(TaskDependency("b"),)),
               CompanyTask("b", "w", "B", "act", "r", dependencies=(TaskDependency("a"),))))
    probe.refused("company_plan_cycle_refused", cyclic_plan.validate, ValueError,
                  contains="cycle in company task DAG")

    probe.tag("DAG", "BCC-TASK-GRAPH", "COMPANY-PLAN")
    probe.count(effects=10, recoveries=1)   # 10 nodes driven to a terminal status; 1 retry recovery
    return probe.finish()


# --------------------------------------------------------- adaptive compute
def adaptive_reasoning(seed: int) -> dict:
    probe = CaseProbe("sandbox.adaptive_reasoning", "adaptive_reasoning", seed)
    from bossman import db, events, runner, uncertainty as unc
    from bossman.compute_budget import (MANDATORY_ACTIONS, ComputeLevel, evr, may_skip, select_level,
                                        should_continue_reasoning, voi)
    from bossman.config import settings
    from bossman.signals import DecisionSignals

    # The production entrypoint runner._select_compute; its only external touches are
    # one Postgres row (prior failed runs) and the event bus — both scripted here.
    prior_failures = {1: 2, 2: 0}
    emitted: list[list[str]] = []

    async def _fetchrow(sql: str, *args):
        assert "FROM runs" in sql and "status='failed'" in sql
        return {"n": prior_failures.get(args[0], 0)}

    saved = (settings.adaptive_compute, db.fetchrow, events.emit)
    try:
        settings.adaptive_compute = True                 # owner flag, OFF by default
        db.fetchrow = _fetchrow
        events.emit = lambda name, **kw: emitted.append([name, kw.get("level")])
        hard = {"id": 1, "agent": "analyst",
                "text": "сначала проанализируй рынок, затем создай отчёт и потом сравни"}
        lvl_hard, why_hard = asyncio.run(runner._select_compute(hard))
        lvl_triv, _ = asyncio.run(runner._select_compute(
            {"id": 2, "agent": "analyst", "text": "посчитай 2+2"}))
        settings.adaptive_compute = False
        off = asyncio.run(runner._select_compute(hard))
    finally:
        settings.adaptive_compute, db.fetchrow, events.emit = saved

    probe.positive("production_selects_deep_level_for_multistep_task",
                   [lvl_hard.name, list(why_hard)],
                   ["C2_DEEP", ["complexity/uncertainty среднее -> C2"]])
    probe.positive("compute_level_decision_is_emitted", emitted,
                   [["task.compute_level", "C2_DEEP"], ["task.compute_level", "C0_FAST"]])
    probe.negative("deep_reasoning_refused_for_trivial_task",
                   {"level": lvl_triv.name, "escalated": int(lvl_triv) >= int(ComputeLevel.C2_DEEP)},
                   {"level": "C0_FAST", "escalated": False})
    probe.negative("controller_inert_while_owner_flag_is_off", [off[0], list(off[1])], [None, []])

    # Escalation: verifier disagreement + contradictions raise system uncertainty to C3.
    u = unc.estimate(evidence_gap=0.8, contradiction=1.0, verifier_failure=1.0, staleness=1.0,
                     task_class="analyst")
    escalated, _ = select_level(DecisionSignals(uncertainty=u.score))
    probe.positive("verifier_disagreement_escalates_compute",
                   [round(u.score, 3), escalated.name], [0.7, "C3_MULTI_CANDIDATE"])
    probe.positive("irreversible_risk_forces_max_verification",
                   select_level(DecisionSignals(risk=0.9, resource_budget=0.0))[0].name,
                   "C4_MAX_VERIFICATION")
    clamped, clamp_why = select_level(DecisionSignals(uncertainty=u.score, resource_budget=0.0))
    probe.negative("escalation_refused_when_budget_exhausted", [clamped.name, clamp_why[-1]],
                   ["C1_NORMAL", "бюджет почти исчерпан -> не выше C1 (кроме high-risk)"])

    # Stop rule: reason on while expected value is positive, stop on diminishing returns.
    first, extra = evr(0.6, delta_quality=0.5, token_cost=0.1), evr(0.1, delta_quality=0.1, token_cost=0.05)
    probe.positive("reasoning_continues_while_evr_positive",
                   [round(first, 3), should_continue_reasoning(first)], [0.2, True])
    probe.negative("reasoning_stops_on_diminishing_returns",
                   [round(extra, 3), should_continue_reasoning(extra)], [-0.04, False])
    probe.positive("voi_prices_an_extra_observation",
                   round(voi(expected_uncertainty_after=0.1, uncertainty_now=0.6, cost=0.1), 3), 0.4)

    # Manipulation guards: a confident model cannot talk the system out of verification.
    base = unc.estimate(evidence_gap=0.8, risk=0.5)
    probe.negative("high_self_confidence_cannot_lower_uncertainty",
                   [base.score, unc.apply_model_confidence(base, 1.0).score], [0.25, 0.25])
    probe.positive("low_self_confidence_raises_uncertainty",
                   round(unc.apply_model_confidence(base, 0.0).score, 3), 0.35)
    probe.negative("economics_never_skips_mandatory_verification",
                   {a: may_skip(a, -999.0) for a in sorted(MANDATORY_ACTIONS)},
                   {a: False for a in sorted(MANDATORY_ACTIONS)})
    probe.positive("optional_action_skipped_when_voi_nonpositive", may_skip("retrieval", -0.2), True)
    probe.refused("shared_signal_state_is_immutable",
                  lambda: setattr(DecisionSignals(uncertainty=0.1), "uncertainty", 0.0),
                  dataclasses.FrozenInstanceError, contains="cannot assign to field 'uncertainty'")

    probe.tag("ADAPTIVE-COMPUTE", "UNCERTAINTY", "EVR-VOI")
    probe.count(effects=0, recoveries=1)    # pure controller; escalation-after-failure is the recovery
    return probe.finish()


# ---------------------------------------------------------------- verifier
def verifier(seed: int) -> dict:
    probe = CaseProbe("sandbox.verifier", "verifier", seed)
    from bossman.apprentice.errors import VerificationFailed
    from bossman.apprentice.skills import (EvidenceBinding, SelfVerificationRefused,
                                           attach_verification)
    from bossman.company import synthetic_seo as seo
    from bossman.company.runtime import CompanyRuntime
    from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState,
                                                  Observation, new_id)
    from bossman.computer_operator.verifier import Verifier
    from bossman.deep_fix import Evidence, Principal

    # (1) real work, re-observed by an independent verifier -> VERIFIED
    report, site, rt = seo.run_demo()
    probe.positive("fresh_observation_verifies_real_work",
                   [report.task_states["seo-fix-titles"],
                    rt.state.outcomes["seo-fix-titles"].verification.status, site.writes],
                   ["DONE", "VERIFIED", 6])
    # (2) a confidently-wrong self-report is contradicted by the fresh re-read
    lie, lie_site, lie_rt = seo.run_demo(honest=False)
    probe.negative("confidently_wrong_self_report_rejected",
                   [lie.status, lie.task_states["seo-fix-titles"], lie_site.writes,
                    lie_rt.state.outcomes["seo-fix-titles"].verification.status],
                   ["FAILED", "FAILED", 0, "FAILED"])
    # (3) unverifiable work never gets promoted to VERIFIED
    none_rt = CompanyRuntime(seo.build_plan(), executor=seo.make_executor(seo.default_site()),
                             verifier=None, synthetic=True)
    probe.negative("self_report_without_verifier_is_unverified",
                   [none_rt.run().status,
                    none_rt.state.outcomes["seo-fix-titles"].verification.reason],
                   ["UNVERIFIED", "no verifier injected — self-report is not evidence"])
    claim_rt = CompanyRuntime(seo.build_plan(), executor=seo.make_executor(seo.default_site()),
                              verifier=lambda task, result: "VERIFIED", synthetic=True)
    probe.negative("bare_verified_string_is_not_evidence",
                   [claim_rt.run().status,
                    claim_rt.state.outcomes["seo-fix-titles"].verification.reason],
                   ["UNVERIFIED", "verifier returned str"])

    # (4) the ComputerOperatorManager default verifier
    def obs(summary: str = "") -> Observation:
        return Observation(id=new_id("obs"), created_at=0.0,
                           foreground={"title": "", "app": "", "url": ""}, summary=summary,
                           ui_tree={"elements": []}, generation=1)
    v = Verifier()
    good = v.verify(ComputerAction.make(ActionKind.CLICK,
                                        expected=ExpectedState(contains_text="saved")), obs("file saved"))
    probe.positive("postcondition_met_is_verified", [good.ok, good.reason], [True, "verified"])
    bare = v.verify(ComputerAction.make(ActionKind.CLICK), obs())
    probe.negative("mutating_action_without_postcondition_refused", [bare.ok, bare.reason],
                   [False, "mutating action missing postcondition"])
    wrong = v.verify(ComputerAction.make(ActionKind.CLICK,
                                         expected=ExpectedState(absent_text="error")), obs("ERROR happened"))
    probe.negative("contradicted_postcondition_refused", [wrong.ok, wrong.reason],
                   [False, "postcondition failed"])

    # (5) independence + evidence binding on the learning record
    producer = Principal("agent:apprentice", model_id="qwen", role="coder", run_id=f"run-{seed}")
    indep = Principal("verifier:pytest", model_id="pytest", role="verifier",
                      run_id=f"chk-{seed}", independence_class="external_tool")
    binding = EvidenceBinding(task_id="skill.notes.save", head_sha="abc123", environment="env:sandbox")
    now = 1_700_000_000.0

    def ev(*, head: str = "abc123", passed: bool = True, principal: str = "verifier:pytest") -> Evidence:
        return Evidence(kind="test", detail="acceptance suite", passed=passed, source="pytest",
                        at=now, collected_at=now, task_id="skill.notes.save", run_id="",
                        principal_id=principal, environment="env:sandbox", head_sha=head,
                        expected="pass", actual="pass")

    def attach(**kw):
        args = dict(producer=producer, verifier=indep, evidence=[ev()], binding=binding, now=now)
        return lambda: attach_verification({}, **(args | kw))

    rec = attach()()   # the accepted path: independent verifier + fresh bound passing evidence
    probe.positive("independent_verifier_marks_record_verified",
                   [rec["learning_status"], rec["verified_by"]], ["VERIFIED", ["verifier:pytest"]])
    probe.refused("self_verification_refused", attach(verifier=producer),
                  SelfVerificationRefused, contains="is not independent of producer")
    probe.refused("producer_observed_evidence_refused",
                  attach(evidence=[ev(principal=producer.principal_id)]),
                  SelfVerificationRefused, contains="observed by the producer")
    probe.refused("evidence_bound_to_another_head_refused", attach(evidence=[ev(head="deadbeef")]),
                  VerificationFailed, contains="bound to another head")
    probe.refused("failing_evidence_refused", attach(evidence=[ev(passed=False)]),
                  VerificationFailed, contains="did not pass")
    probe.refused("absent_evidence_refused", attach(evidence=[]),
                  VerificationFailed, contains="no evidence")

    probe.tag("VERIFICATION", "FRESH-OBSERVATION", "INDEPENDENCE")
    probe.count(effects=site.writes)   # real page writes the honest run made and the verifier re-read
    return probe.finish()


CASES = {"sandbox.dag_compiler": dag_compiler,
         "sandbox.adaptive_reasoning": adaptive_reasoning,
         "sandbox.verifier": verifier}
