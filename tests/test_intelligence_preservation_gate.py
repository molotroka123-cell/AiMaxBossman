from __future__ import annotations

import pytest

from tools.intelligence_preservation_gate import GateConfig, evaluate

METRICS = (
    "reasoning_accuracy",
    "coding_correctness",
    "structured_output_accuracy",
    "unknown_task_adaptation",
    "tool_selection_accuracy",
    "schema_argument_accuracy",
    "long_context_accuracy",
    "memory_retrieval_accuracy",
    "computer_use_planning_accuracy",
    "task_completion_rate",
    "hallucination_rate",
)


def _mode(score: float = 0.80, hallucination: float = 0.05, samples: int = 200):
    payload = {name: {"score": score, "samples": samples} for name in METRICS}
    payload["hallucination_rate"] = {"score": hallucination, "samples": samples}
    return payload


def _payload():
    return {
        "model": "fixture-model",
        "dataset_id": "anti-dumbness-fixture-v1",
        "modes": {"raw": _mode(), "system": _mode(), "context": _mode(), "full": _mode()},
    }


def test_equal_quality_passes():
    report = evaluate(_payload())
    assert report["status"] == "PASS"
    assert report["scores"]["full_core_retention"] == pytest.approx(1.0)


def test_more_than_two_percent_core_regression_is_no_go():
    payload = _payload()
    for name in ("reasoning_accuracy", "coding_correctness", "structured_output_accuracy", "unknown_task_adaptation"):
        payload["modes"]["full"][name]["score"] = 0.78
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "FULL_CORE_REGRESSION" for f in report["findings"])


def test_context_pollution_isolated_as_context_regression():
    payload = _payload()
    for name in ("reasoning_accuracy", "coding_correctness", "structured_output_accuracy", "unknown_task_adaptation"):
        payload["modes"]["context"][name]["score"] = 0.77
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "CONTEXT_CORE_REGRESSION" for f in report["findings"])


def test_tool_overload_is_no_go_even_when_reasoning_is_preserved():
    payload = _payload()
    payload["modes"]["context"]["tool_selection_accuracy"]["score"] = 0.90
    payload["modes"]["full"]["tool_selection_accuracy"]["score"] = 0.89
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "TOOL_REGRESSION" for f in report["findings"])


def test_hallucination_regression_blocks_release():
    payload = _payload()
    payload["modes"]["full"]["hallucination_rate"]["score"] = 0.06
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "HALLUCINATION_REGRESSION" for f in report["findings"])


def test_missing_mode_is_insufficient_not_pass():
    payload = _payload()
    del payload["modes"]["context"]
    with pytest.raises(ValueError, match="missing mode"):
        evaluate(payload)


def test_missing_metric_is_insufficient_not_pass():
    payload = _payload()
    del payload["modes"]["raw"]["coding_correctness"]
    with pytest.raises(ValueError, match="missing metric"):
        evaluate(payload)


def test_tiny_sample_cannot_pass():
    payload = _payload()
    payload["modes"]["raw"]["reasoning_accuracy"]["samples"] = 3
    with pytest.raises(ValueError, match="insufficient samples"):
        evaluate(payload, GateConfig(min_samples_per_metric=20))


def test_zero_raw_hallucination_must_stay_zero():
    payload = _payload()
    payload["modes"]["raw"]["hallucination_rate"]["score"] = 0.0
    payload["modes"]["full"]["hallucination_rate"]["score"] = 0.01
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "HALLUCINATION_REGRESSION" for f in report["findings"])
