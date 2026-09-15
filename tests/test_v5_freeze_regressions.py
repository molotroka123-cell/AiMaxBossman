from __future__ import annotations

import pytest

from bossman_shared.objective_canary import (
    FAILED,
    CanaryError,
    CanaryOutcome,
    evaluate_canary,
    plan_canary,
)
from bossman_shared.objective_improvement import CandidateImprovement, REQUIRED_STAGES
from bossman_shared.objective_promotion import (
    Outcome,
    PromotionError,
    Split,
    Task,
    authorize,
    measure,
)


class _Ledger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def consume(self, key: str, consumer: str) -> str | None:
        self.calls.append((key, consumer))
        return None


def _tasks() -> list[Task]:
    return [Task(f"task-{i:02d}", "python.refactor") for i in range(20)]


def _run(variant: str, task: Task) -> Outcome:
    return Outcome(task.task_id, variant == "candidate", 1.0 if variant == "candidate" else 0.0)


def _measurement():
    tasks = _tasks()
    return measure(
        tasks,
        candidate_id="skill-x",
        candidate_version="v2",
        baseline_version="v1",
        applicability_version="scope-v1",
        run=_run,
        split=Split(
            tuple(t.task_id for t in tasks[:15]),
            tuple(t.task_id for t in tasks[15:]),
            "fixed",
        ),
    )


def test_canary_failure_is_sticky_when_later_report_says_healthy():
    plan = plan_canary([f"o-{i}" for i in range(15)], revision_digest="rev-1", now=1.0)
    target = plan.cohort[0]
    outcomes = [CanaryOutcome(target, False, "e-fail"), CanaryOutcome(target, True, "e-late")]
    outcomes.extend(CanaryOutcome(oid, True, f"e-{oid}") for oid in plan.cohort[1:])

    verdict = evaluate_canary(plan, outcomes)

    assert verdict.state == FAILED
    assert target in verdict.unhealthy


@pytest.mark.parametrize("health", [0, 1, "false", "true", [], {}])
def test_canary_health_rejects_non_boolean_surrogates(health):
    plan = plan_canary([f"o-{i}" for i in range(15)], revision_digest="rev-2", now=1.0)
    with pytest.raises(CanaryError, match="healthy must be bool or None"):
        evaluate_canary(plan, [CanaryOutcome(plan.cohort[0], health)])


@pytest.mark.parametrize(
    "split",
    [
        Split(tuple(["task-00"] * 15), tuple(["task-01"] * 5), "dup"),
        Split(tuple(f"task-{i:02d}" for i in range(15)), tuple(f"task-{i:02d}" for i in range(14, 19)), "overlap"),
        Split(tuple(f"task-{i:02d}" for i in range(15)), tuple(f"task-{i:02d}" for i in range(15, 19)), "omit"),
    ],
)
def test_promotion_split_must_be_unique_disjoint_complete_partition(split):
    calls = {"n": 0}

    def should_not_run(variant: str, task: Task) -> Outcome:
        calls["n"] += 1
        return _run(variant, task)

    with pytest.raises(PromotionError):
        measure(
            _tasks(),
            candidate_id="skill-x",
            candidate_version="v2",
            baseline_version="v1",
            applicability_version="scope-v1",
            run=should_not_run,
            split=split,
        )
    assert calls["n"] == 0, "invalid evidence partition must be refused before measuring"


def test_promotion_duplicate_declared_tasks_are_refused_even_with_explicit_split():
    tasks = _tasks()
    tasks[-1] = Task(tasks[0].task_id, tasks[0].applicability)
    with pytest.raises(PromotionError, match="duplicate task_id"):
        measure(
            tasks,
            candidate_id="skill-x",
            candidate_version="v2",
            baseline_version="v1",
            applicability_version="scope-v1",
            run=_run,
            split=Split(
                tuple(f"task-{i:02d}" for i in range(15)),
                tuple(f"task-{i:02d}" for i in range(15, 19)),
                "duplicate-input",
            ),
        )


def test_promotion_measurement_is_bound_to_current_baseline_before_ledger_consumption():
    measurement = _measurement()
    candidate = CandidateImprovement(
        kind="skill",
        current_version="different-live-baseline",
        candidate_version="v2",
        hypothesis="measured improvement",
        completed_stages=REQUIRED_STAGES,
    )
    ledger = _Ledger()

    verdict = authorize(
        measurement,
        candidate,
        ledger=ledger,
        applicability_version="scope-v1",
        applicability_scope=("python.refactor",),
        retention=1.0,
        retention_evidence_ref="intelligence_preservation/paired/" + "a" * 64,
        security_pass=True,
        rollback_available=True,
    )

    assert verdict.authorized is False
    assert verdict.reason == "evidence_measured_on_another_baseline"
    assert ledger.calls == []


@pytest.mark.parametrize("score", [float("inf"), float("-inf"), float("nan")])
def test_promotion_outcome_rejects_all_nonfinite_scores(score):
    with pytest.raises(PromotionError, match="finite"):
        Outcome("task-x", True, score)
