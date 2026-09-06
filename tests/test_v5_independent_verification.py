"""Independent negative contracts for PR37 at 9cb1fb4.

These are component/helper tests, NOT application bypass or freeze proof.
No production caller, desktop, model, payment or standing autonomy is activated.
Failures deliberately assert the required safe result; no xfail/skip is used.
Promotion scores come from a separate oracle checking real local CSV outputs.
The retention reference in positive controls is ONLY a syntactic test fixture.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from itertools import permutations

import pytest

from bossman_shared import objective_canary as canary
from bossman_shared import objective_fairness as fairness
from bossman_shared import objective_improvement as improvement
from bossman_shared import objective_promotion as promotion


@pytest.fixture(autouse=True)
def evidence_tier(record_property):
    record_property("evidence_tier", "MOCK")
    record_property("scope", "real_helper_with_fixture_inputs_not_production_caller")


def plan():
    return canary.plan_canary([f"obj-{n}" for n in range(15)],
                              revision_digest="candidate-revision-2", now=100.0)


def healthy_rows(p):
    # References are fixture strings, never attested live evidence.
    return [canary.CanaryOutcome(oid, True, f"fixture/{oid}") for oid in p.cohort]


def test_canary_failure_cannot_be_erased_by_later_healthy_report():
    p = plan()
    rows = healthy_rows(p)
    first = canary.CanaryOutcome(p.cohort[0], False, "fixture/failure")
    verdict = canary.evaluate_canary(p, [first, *rows])
    assert verdict.state == canary.FAILED, verdict


def test_canary_verdict_is_failure_monotone_under_all_report_permutations():
    p = plan()
    rows = [canary.CanaryOutcome(p.cohort[0], False, "fixture/failure"), *healthy_rows(p)]
    observed = {canary.evaluate_canary(p, order).state for order in permutations(rows)}
    assert observed == {canary.FAILED}, observed


def test_canary_identical_healthy_replay_is_idempotent_control():
    p = plan()
    rows = healthy_rows(p)
    assert canary.evaluate_canary(p, rows) == canary.evaluate_canary(p, [*rows, rows[0]])


@pytest.mark.parametrize("reference", ["", "not-an-evidence-record", "revision-1/foreign-revision-proof"])
def test_canary_unattested_or_foreign_evidence_does_not_establish_success(reference):
    """Helper boundary finding only: no claim about a nonexistent resolver."""
    p = plan()
    rows = [canary.CanaryOutcome(oid, True, reference) for oid in p.cohort]
    verdict = canary.evaluate_canary(p, rows)
    assert not verdict.passed, verdict


@pytest.mark.parametrize("untyped", [0, 1, "false", "true"])
def test_canary_health_requires_boolean_not_truthy_or_falsey_surrogate(untyped):
    p = plan()
    try:
        rows = [canary.CanaryOutcome(oid, untyped, "fixture/ref") for oid in p.cohort]
        verdict = canary.evaluate_canary(p, rows)
    except canary.CanaryError:
        return
    assert not verdict.passed, verdict


def test_canary_silent_member_stays_pending_control():
    p = plan()
    verdict = canary.evaluate_canary(p, healthy_rows(p)[:-1])
    assert verdict.state == canary.PENDING
    assert verdict.silent == (p.cohort[-1],)
    assert not canary.may_activate_broadly(verdict)[0]


def test_canary_failure_dominates_silence_control():
    p = plan()
    verdict = canary.evaluate_canary(p, [canary.CanaryOutcome(p.cohort[0], False)])
    assert verdict.state == canary.FAILED
    assert not canary.may_activate_broadly(verdict)[0]


def test_canary_unknown_health_stays_pending_control():
    p = plan()
    rows = healthy_rows(p)
    rows[0] = canary.CanaryOutcome(p.cohort[0], None)
    assert canary.evaluate_canary(p, rows).state == canary.PENDING


def test_canary_outsider_is_rejected_control():
    with pytest.raises(canary.CanaryError):
        canary.evaluate_canary(plan(), [canary.CanaryOutcome("outsider", True)])


def test_canary_cohort_is_order_independent_control():
    population = [f"obj-{n}" for n in range(15)]
    a = canary.plan_canary(population, revision_digest="r2", now=1)
    b = canary.plan_canary(reversed(population), revision_digest="r2", now=1)
    assert a == b and a.rest


class MemoryLedger:
    """Test-only port. Durable behavior is tested with the canonical core ledger."""
    def __init__(self):
        self.records = {}

    def consume(self, key, consumer):
        previous = self.records.setdefault(key, consumer)
        return None if previous == consumer else "spent"


def tasks():
    return [promotion.Task(f"csv-{n:02}", "fixture.csv") for n in range(30)]


def independent_csv_verifier(variant, task):
    """Runs baseline/candidate; scores actual output against a separate oracle."""
    n = int(task.task_id.split("-")[1])
    quoted = n % 2 == 0
    text = f'"left,{n}",right' if quoted else f"left{n},right"
    expected = [f"left,{n}" if quoted else f"left{n}", "right"]
    actual = text.split(",") if variant == promotion.BASELINE else next(csv.reader([text]))
    passed = actual == expected
    return promotion.Outcome(task.task_id, passed, float(passed))


def measurement(*, rows=None, split=None, run=independent_csv_verifier):
    return promotion.measure(
        tasks() if rows is None else rows, candidate_id="fixture-csv-parser",
        candidate_version="csv-v2", baseline_version="split-v1", applicability_version="scope-v1",
        run=run, split=split)


def candidate(**changes):
    item = improvement.CandidateImprovement(
        "skill", "split-v1", "csv-v2", "Respect quoted CSV fields", improvement.REQUIRED_STAGES)
    return replace(item, **changes)


def authorize(m, *, item=None, ledger=None, **changes):
    kw = dict(ledger=ledger or MemoryLedger(), applicability_version="scope-v1",
              applicability_scope=("fixture.csv",), retention=1.0,
              retention_evidence_ref="intelligence_preservation/paired/" + "a" * 64,
              security_pass=True, rollback_available=True)
    kw.update(changes)
    return promotion.authorize(m, candidate() if item is None else item, **kw)


def test_promotion_local_outputs_are_independently_scored_control():
    calls = []
    def run(variant, task):
        calls.append((variant, task.task_id))
        return independent_csv_verifier(variant, task)
    m = measurement(run=run)
    assert len(calls) == 60 and len(set(calls)) == 60
    assert not set(m.split.measured) & set(m.split.holdout)
    assert m.measured[promotion.CANDIDATE].pass_rate == 1.0
    assert 0.0 < m.measured[promotion.BASELINE].pass_rate < 1.0
    # Eligibility only; this is neither real retention nor an activated skill.
    assert authorize(m).authorized


def test_promotion_overlapping_holdout_must_be_rejected():
    rows = tasks()
    ids = tuple(t.task_id for t in rows)
    split = promotion.Split(ids[:20], ids[:10], "malformed-overlap")
    try:
        m = measurement(rows=rows, split=split)
        verdict = authorize(m)
    except promotion.PromotionError:
        return
    assert not verdict.authorized, verdict


def test_promotion_one_task_cannot_be_counted_as_twenty_samples():
    task = tasks()[0]  # quoted CSV: baseline fails, candidate passes
    try:
        m = measurement(rows=[task], split=promotion.Split((task.task_id,) * 15,
                                                          (task.task_id,) * 5, "inflated"))
        verdict = authorize(m)
    except promotion.PromotionError:
        return
    assert not verdict.authorized, (m.facts, m.measured, m.holdout, verdict)


def test_promotion_duplicate_task_rows_with_explicit_split_must_be_rejected():
    rows = tasks()
    split = promotion.split_tasks(rows, material="fixed")
    with pytest.raises(promotion.PromotionError):
        measurement(rows=[*rows, rows[0]], split=split)


def test_promotion_default_split_rejects_duplicate_task_ids_control():
    rows = tasks()
    with pytest.raises(promotion.PromotionError):
        measurement(rows=[*rows, rows[0]])


def test_promotion_explicit_split_cannot_silently_omit_tasks():
    rows = tasks()
    ids = tuple(t.task_id for t in rows)
    with pytest.raises(promotion.PromotionError):
        measurement(rows=rows, split=promotion.Split(ids[:15], ids[15:20], "missing-ten"))


def test_promotion_measurement_must_match_the_current_baseline():
    m = measurement()
    verdict = authorize(m, item=candidate(current_version="different-live-baseline"))
    assert not verdict.authorized, verdict


@pytest.mark.parametrize("version", ["scope-v0", "scope-v2"])
def test_promotion_changed_applicability_version_is_refused_control(version):
    assert not authorize(measurement(), applicability_version=version).authorized


def test_promotion_foreign_scope_is_refused_control():
    assert not authorize(measurement(), applicability_scope=("fixture.other",)).authorized


def test_promotion_wrong_candidate_version_is_refused_control():
    assert not authorize(measurement(), item=candidate(candidate_version="csv-v3")).authorized


@pytest.mark.parametrize("reference", ["", "plain-number", "intelligence_preservation/paired/not-a-hash"])
def test_promotion_missing_or_malformed_retention_is_refused_control(reference):
    assert not authorize(measurement(), retention_evidence_ref=reference).authorized


@pytest.mark.parametrize("changes", [{"widens_permissions": True}, {"touches_trust_kernel": True},
                                      {"kind": "treasury"}])
def test_promotion_refusal_does_not_expand_authority_or_consume_evidence_control(changes):
    ledger = MemoryLedger()
    assert not authorize(measurement(), item=candidate(**changes), ledger=ledger).authorized
    assert ledger.records == {}


def test_promotion_holdout_regression_is_refused_control():
    rows = tasks()
    split = promotion.split_tasks(rows, material="holdout-regression")
    holdout = set(split.holdout)
    def run(variant, task):
        # Actual independent oracle, with a deliberately broken candidate lane on holdout.
        outcome = independent_csv_verifier(variant, task)
        if variant == promotion.CANDIDATE and task.task_id in holdout:
            return promotion.Outcome(task.task_id, False, 0.0)
        return outcome
    assert not authorize(measurement(rows=rows, split=split, run=run)).authorized


@pytest.mark.parametrize("score", [float("inf"), float("-inf"), float("nan")])
def test_promotion_outcome_rejects_nonfinite_score(score):
    with pytest.raises(promotion.PromotionError):
        promotion.Outcome("csv-00", True, score)


def test_fairness_starved_candidate_precedes_new_high_priority_control():
    rows = [fairness.Candidate("old", -100, 0), fairness.Candidate("new", 1000, 901)]
    assert fairness.select(rows, now=901).objective_id == "old"


def test_fairness_quota_is_not_overridden_by_priority_or_aging_control():
    rows = [fairness.Candidate("hot", 1000, 0, admissions_in_window=3),
            fairness.Candidate("cold", 0, 0)]
    assert fairness.select(rows, now=2000).objective_id == "cold"


def test_fairness_cooldown_is_not_overridden_by_starvation_control():
    rows = [fairness.Candidate("cooling", 1000, 0, cooldown_until=2001),
            fairness.Candidate("ready", 0, 1000)]
    assert fairness.select(rows, now=2000).objective_id == "ready"


def test_fairness_priority_change_cannot_displace_an_older_starved_goal_control():
    rows = [fairness.Candidate("older", -1000, 0), fairness.Candidate("newer", 100000, 1)]
    assert fairness.select(rows, now=1000).objective_id == "older"


def test_fairness_equal_inputs_have_identical_order_control():
    rows = [fairness.Candidate(f"obj-{i}", i, 0) for i in range(20)]
    assert fairness.rank(rows, now=1000) == fairness.rank(reversed(rows), now=1000)


def test_fairness_admission_resets_wait_age_control():
    rows = [fairness.Candidate("just-served", 1000, 0, last_admitted_at=1000),
            fairness.Candidate("waiting", -1000, 0)]
    assert fairness.select(rows, now=1001).objective_id == "waiting"
