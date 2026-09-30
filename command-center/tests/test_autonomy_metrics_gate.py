"""Metrics gate: the constitution quality rule (target improved, protected within thresholds)."""
from __future__ import annotations

import math

import pytest

from bcc.autonomy.metrics_gate import decide, direction_of

PROTECTED = ("task_success", "latency_ms", "cost_usd", "redteam_pass_rate")
BEFORE = {"identity_redteam.leaks": 4, "task_success": 0.90, "latency_ms": 800, "cost_usd": 0.0,
          "redteam_pass_rate": 0.95}


def after(**kw):
    return {**BEFORE, "identity_redteam.leaks": 0, **kw}


def test_directions():
    assert direction_of("identity_redteam.leaks") == "lower"
    assert direction_of("latency_ms") == "lower" and direction_of("cost_usd") == "lower"
    assert direction_of("task_success") == "higher" and direction_of("redteam_pass_rate") == "higher"


def test_accept_when_target_improves_and_protected_hold():
    v = decide(BEFORE, after(), "identity_redteam.leaks", PROTECTED, {"latency_ms": 50})
    assert v.decision == "ACCEPT" and v.verdict == "ACCEPT" and v.accepted and v.target.change == 4
    assert v.as_dict()["verdict"] == "ACCEPT" and v.as_dict()["accepted"] is True


def test_reject_when_target_does_not_improve():
    v = decide(BEFORE, {**BEFORE}, "identity_redteam.leaks", PROTECTED)
    assert v.decision == "REJECT" and "not improved" in v.reasons[0]
    v = decide(BEFORE, after(**{"identity_redteam.leaks": 5}), "identity_redteam.leaks", PROTECTED)
    assert v.decision == "REJECT"


def test_min_improvement():
    th = {"identity_redteam.leaks": {"min_improvement": 5}}
    assert decide(BEFORE, after(), "identity_redteam.leaks", (), th).decision == "REJECT"
    th = {"task_success": {"min_improvement": 0.05, "relative": True}}
    assert decide(BEFORE, {**BEFORE, "task_success": 0.99}, "task_success", ()).accepted
    assert decide(BEFORE, {**BEFORE, "task_success": 0.92}, "task_success", (), th).decision == "REJECT"


def test_protected_regression_beyond_threshold_rejects():
    v = decide(BEFORE, after(latency_ms=900), "identity_redteam.leaks", PROTECTED, {"latency_ms": 50})
    assert v.decision == "REJECT" and any("latency_ms" in r for r in v.reasons)
    assert decide(BEFORE, after(latency_ms=840), "identity_redteam.leaks", PROTECTED, {"latency_ms": 50}).accepted
    v = decide(BEFORE, after(task_success=0.80), "identity_redteam.leaks", PROTECTED,
               {"task_success": {"max_regression": 0.05, "relative": True}})
    assert v.decision == "REJECT"


def test_default_threshold_is_zero_regression():
    assert decide(BEFORE, after(cost_usd=0.01), "identity_redteam.leaks", PROTECTED).decision == "REJECT"


def test_deployed_failures_roll_back():
    v = decide(BEFORE, after(redteam_pass_rate=0.5), "identity_redteam.leaks", PROTECTED, deployed=True)
    assert v.decision == "ROLLBACK"


@pytest.mark.parametrize("bad", [None, "0.9", math.nan, math.inf, True])
def test_missing_or_bad_values_fail_closed(bad):
    v = decide(BEFORE, after(task_success=bad), "identity_redteam.leaks", PROTECTED)
    assert v.decision == "REJECT" and any("task_success" in r for r in v.reasons)
    v = decide({}, after(), "identity_redteam.leaks", ())
    assert v.decision == "REJECT"


def test_no_target_or_target_protected_or_bad_threshold():
    assert decide(BEFORE, after(), "", PROTECTED).decision == "REJECT"
    assert decide(BEFORE, after(), "task_success", PROTECTED).decision == "REJECT"
    v = decide(BEFORE, after(), "identity_redteam.leaks", PROTECTED, {"latency_ms": {"direction": "sideways"}})
    assert v.decision == "REJECT" and "invalid thresholds" in v.reasons[0]
