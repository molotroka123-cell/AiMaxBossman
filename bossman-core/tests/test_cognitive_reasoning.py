"""Reasoning 10/10: D-оценка, режимы, multi-hypothesis, stop, Fable EV."""
from __future__ import annotations

import pytest

from bossman.cognitive.reasoning import (
    ComplexitySignals,
    FableOptions,
    Hypothesis,
    ModeThresholds,
    MultiHypothesisTracker,
    ReasoningController,
    ReasoningMode,
    StopSignals,
    ThoughtState,
    calibrate_thresholds,
    complexity_score,
    fable_expected_value,
    should_call_fable,
    should_stop,
)
from bossman.cognitive.storage import CognitiveStore


def test_complexity_formula_weights():
    d = complexity_score(ComplexitySignals(novelty=1, graph_size=1, risk=1,
                                           uncertainty=1, conflict=1,
                                           past_failures=1, budget_pressure=1))
    assert abs(d - 1.0) < 1e-9
    d0 = complexity_score(ComplexitySignals())
    assert d0 == 0.0


def test_mode_routing_priority():
    th = ModeThresholds()
    assert th.pick(0.1) is ReasoningMode.FAST
    assert th.pick(0.4) is ReasoningMode.STANDARD
    assert th.pick(0.8) is ReasoningMode.DEEP
    assert th.pick(0.1, irreversible=True) is ReasoningMode.HUMAN_APPROVAL
    assert th.pick(0.1, security_sensitive=True) is ReasoningMode.ADVERSARIAL
    assert th.pick(0.8, unknowns=3) is ReasoningMode.MULTI_HYPOTHESIS


def test_calibrate_thresholds_splits_classes():
    th = calibrate_thresholds([(0.1, "FAST"), (0.15, "FAST"),
                               (0.4, "STANDARD"), (0.45, "STANDARD"),
                               (0.8, "DEEP"), (0.85, "DEEP")])
    assert 0.15 < th.fast_max < 0.4
    assert 0.45 < th.deep < 0.8


def test_multi_hypothesis_confirms_root_cause():
    tr = MultiHypothesisTracker([
        Hypothesis("h1", "race in cache", 0.5, "run with cache off"),
        Hypothesis("h2", "retry storm", 0.3, "check retry counters"),
        Hypothesis("h3", "clock skew", 0.2, "compare timestamps"),
    ])
    first = tr.cheapest_informative_test({"h1": 5.0, "h2": 1.0, "h3": 2.0})
    assert first.hid == "h2"
    tr.observe("h2", supports=False, strength=0.8)  # опровергнута
    assert all(h.hid != "h2" for h in tr.live())
    tr.observe("h1", supports=True, strength=0.9)
    tr.observe("h1", supports=True, strength=0.9)
    assert tr.confirmed_root_cause() is not None
    assert tr.confirmed_root_cause().hid == "h1"


def test_stop_rule_honest_blocked():
    assert should_stop(StopSignals(verified=True)).reason == "verified"
    assert should_stop(StopSignals(approval_required=True)).reason == "approval_required"
    b = should_stop(StopSignals(evidence_insufficient=True))
    assert b.stop and b.reason == "blocked_insufficient_evidence"
    assert should_stop(StopSignals(next_check_cost=5, expected_benefit=1)).reason == "cost_exceeds_benefit"
    assert should_stop(StopSignals()) == should_stop(StopSignals())  # детерминизм
    assert should_stop(StopSignals()).stop is False


def test_fable_ev_and_p0_early():
    o = FableOptions(p_improve=0.5, value=10.0, cost=1.0, latency=1.0, risk=1.0)
    assert fable_expected_value(o) == pytest.approx(2.0)
    assert should_call_fable(o, local_continuation_ev=5.0)["call"] is False
    assert should_call_fable(o, local_continuation_ev=5.0, p0_security=True)["call"] is True


def test_unsupported_certainty_flagged():
    rc = ReasoningController(CognitiveStore(":memory:"))
    bad = ThoughtState(goal="x", confidence=0.95)
    assert rc.unsupported_certainty(bad) is True
    good = ThoughtState(goal="x", confidence=0.95, verified_facts=["v1"])
    assert rc.unsupported_certainty(good) is False
