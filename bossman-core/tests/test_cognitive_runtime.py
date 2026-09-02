"""Сквозной runtime + verify: A/B, holdout, restart, dashboard."""
from __future__ import annotations

from bossman.cognitive.context import ContextItem, Priority
from bossman.cognitive.memory import Tier, WriteEvidence
from bossman.cognitive.reasoning import ComplexitySignals, ThoughtState
from bossman.cognitive.runtime import CognitiveRuntime, RuntimeConfig
from bossman.cognitive.storage import CognitiveStore
from bossman.cognitive.tasks import EnvSnapshot, JournalStep, StepState
from bossman.cognitive.verify import (
    LONGTASK_GATES,
    MEMORY_GATES,
    TrialResult,
    ab_compare,
    evaluate_gates,
    run_holdout,
)


def _ev() -> WriteEvidence:
    return WriteEvidence(True, "verifier-1", "exec-1",
                         "2026-09-01T12:00:00+00:00", True, False)


def test_runtime_end_to_end():
    rt = CognitiveRuntime(RuntimeConfig(db_path=":memory:"))
    rt.begin_task("t1", "fix benchmark race", constraints=["no secrets in logs"])
    th = rt.think("t1", ThoughtState(goal="fix race", unknowns=["u1", "u2", "u3"]),
                  ComplexitySignals(novelty=0.8, risk=0.6, uncertainty=0.7,
                                    conflict=0.8, past_failures=0.5))
    assert th["mode"] in ("DEEP", "MULTI_HYPOTHESIS")
    rec, dec = rt.memory.propose("race fixed by lock ordering", tier=Tier.EPISODIC,
                                 owner_id="u1", project_id="p1", evidence=_ev())
    assert dec.action == "ACCEPT"
    hits = rt.recall("race lock ordering", owner_id="u1", project_id="p1")
    assert hits and hits[0].record.memory_id == rec.memory_id
    out = rt.compile([ContextItem("System invariants", "safety", Priority.P0, "s"),
                      ContextItem("User goal", "fix race", Priority.P0, "u")])
    assert out["raw"] is False and "fix race" in out["text"]
    rt.journal.add_step(JournalStep("t1", "s1", input_hash="in"))
    rt.journal.transition("t1", "s1", StepState.READY)
    d = rt.dashboard()
    assert d["verified_memories"] >= 1
    rt.close()


def test_ab_requires_quality_and_cost_together():
    base = [TrialResult(f"b{i}", i < 5, "v", "e", cost=1.0) for i in range(10)]
    cand = [TrialResult(f"c{i}", i < 9, "v", "e", cost=1.0) for i in range(10)]
    r = ab_compare(base, cand)
    assert r.decision == "SHIP"
    expensive = [TrialResult(f"c{i}", True, "v", "e", cost=100.0) for i in range(10)]
    r2 = ab_compare(base, expensive)
    assert r2.decision in ("HOLD", "REGRESS")
    worse = [TrialResult(f"c{i}", i < 2, "v", "e", cost=0.1) for i in range(10)]
    assert ab_compare(base, worse).decision == "REGRESS"


def test_holdout_gates_evaluation():
    rep = run_holdout("sha-test", [{"x": 1}, {"x": 2}],
                      lambda it: {"LongTaskVerifiedSuccess": 1.0,
                                  "ResumeAccuracy": 1.0,
                                  "DuplicateExternalEffects": 0.0,
                                  "LostVerifiedSteps": 0.0,
                                  "WrongDependencyExecution": 0.0,
                                  "FalseCompletion": 0.0,
                                  "BudgetContinuity": 1.0,
                                  "RecoverySuccess": 1.0},
                      LONGTASK_GATES)
    assert rep.gates_pass is True
    bad = evaluate_gates({"MemoryPrecision": 0.5}, MEMORY_GATES)
    assert bad["pass"] is False


def test_same_verifier_trials_excluded():
    ts = [TrialResult("t1", True, "same", "same"), TrialResult("t2", True, "v", "e")]
    from bossman.cognitive.verify import verified_success_rate
    r = verified_success_rate(ts)
    assert r["n"] == 1 and r["excluded_same_verifier"] == 1
