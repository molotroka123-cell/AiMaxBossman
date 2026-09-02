"""RED-TEAM REPRODUCTIONS (GLM joint attack with Fable call 1).

A1: a non-finite component score must not fabricate benchmark evidence.
    - float('inf') used to clamp to a perfect 1.0 (MEASURED);
    - float('nan') used to silently become the floor;
    both must instead mark the component missing (INSUFFICIENT_EVIDENCE),
    because a corrupted metric is corruption, not an extreme score.
A4 (rejected): sandbox rows cannot self-report evidence — verified is computed
    by the runner (sandbox_row strips 'verified').  Kept here as an anti-regression
    guard: a row that tries to self-report must stay UNVERIFIED.
"""
from __future__ import annotations

from bossman.benchmark.engine import _weighted_geometric_mean, SYSTEM_IQ_WEIGHTS


def _full_parts(value: float) -> dict[str, float | None]:
    return {k: (value if k == "VerifiedSuccess" else 1.0) for k in SYSTEM_IQ_WEIGHTS}


def test_a1_infinite_component_score_is_rejected_not_perfect():
    parts = _full_parts(float("inf"))
    result = _weighted_geometric_mean(parts, SYSTEM_IQ_WEIGHTS)
    assert result["status"] == "INSUFFICIENT_EVIDENCE", result
    assert "VerifiedSuccess" in result["missing_components"]


def test_a1_nan_component_score_is_rejected_not_floor():
    parts = _full_parts(float("nan"))
    result = _weighted_geometric_mean(parts, SYSTEM_IQ_WEIGHTS)
    assert result["status"] == "INSUFFICIENT_EVIDENCE", result
    assert "VerifiedSuccess" in result["missing_components"]


def test_a1_finite_scores_still_measured():
    result = _weighted_geometric_mean(_full_parts(0.9), SYSTEM_IQ_WEIGHTS)
    assert result["status"] == "MEASURED"
    assert result["components"]["VerifiedSuccess"] == 0.9


def test_a4_boolean_corruption_in_component_is_rejected():
    parts = _full_parts(0.9)
    parts["VerifiedSuccess"] = True        # bool is an int subclass: not a score
    result = _weighted_geometric_mean(parts, SYSTEM_IQ_WEIGHTS)
    assert "VerifiedSuccess" in result["missing_components"]
