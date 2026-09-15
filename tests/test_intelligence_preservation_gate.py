"""Intelligence preservation gate: fail-closed, paired, SHA-bound, precision-aware.

Missing lanes/metrics/samples/identity are INSUFFICIENT (ValueError), an observed
regression is NO_GO, and a point estimate above 98% is PASS only when the
conservative lower confidence bound also clears the bar.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.intelligence_preservation_gate import CORE_METRICS, GateConfig, evaluate, wilson

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
SHA = "c" * 40


def _mode(score: float = 0.80, hallucination: float = 0.05, samples: int = 1000, *, paired: bool = True):
    payload = {name: {"score": score, "samples": samples} for name in METRICS}
    payload["hallucination_rate"] = {"score": hallucination, "samples": samples}
    if paired:
        # a genuine paired runner reports discordance versus the baseline lane
        for name in CORE_METRICS:
            payload[name]["paired"] = {"lost": 0, "gained": 0}
    return payload


def _lanes():
    """How a real payload declares it was produced.

    The fixture carries this block because real evidence carries it: the gate
    now asks whether the FULL lane actually RAN the harness, and a fixture that
    omitted the block would only be testing the refusal path by accident.
    """
    return {
        "raw": {"lane": "raw", "kind": "prompt_ablation", "executes_tools": False},
        "system": {"lane": "system", "kind": "prompt_ablation", "executes_tools": False},
        "context": {"lane": "context", "kind": "prompt_ablation", "executes_tools": False},
        "full": {"lane": "full", "kind": "production_execution_loop", "executes_tools": True,
                 "observed": {"model_turns": 42, "executed": 17, "declined": 3,
                              "items_with_executed_tool_call": 12}},
    }


def _payload(samples: int = 1000, *, paired: bool = True):
    return {
        "model": "fixture-model",
        "dataset_id": "anti-dumbness-fixture-v1",
        "evaluated_sha": SHA,
        "lanes": _lanes(),
        "modes": {"raw": _mode(samples=samples, paired=False), "system": _mode(samples=samples, paired=paired),
                  "context": _mode(samples=samples, paired=paired), "full": _mode(samples=samples, paired=paired)},
    }


def _set(payload, mode, name, score):
    """Change a lane score; the fixture's paired zeros would contradict it."""
    payload["modes"][mode][name]["score"] = score
    payload["modes"][mode][name].pop("paired", None)


def test_equal_quality_with_paired_evidence_passes():
    report = evaluate(_payload())
    assert report["status"] == "PASS", report["findings"]
    assert report["scores"]["full_core_retention"] == pytest.approx(1.0)
    assert report["scores"]["core_retention_lower_bound"]["full"] >= 0.98
    assert report["evaluated_sha"] == SHA and report["version"] == 2


def test_equal_point_estimates_without_paired_evidence_are_insufficient_not_pass():
    """1000 unpaired items per metric cannot prove a 2% non-inferiority margin."""
    report = evaluate(_payload(paired=False))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert any(f["code"] == "CORE_RETENTION_PRECISION_INSUFFICIENT" for f in report["findings"])
    assert report["scores"]["full_core_retention"] == pytest.approx(1.0)


def test_paired_zero_discordance_on_too_few_items_is_still_insufficient():
    report = evaluate(_payload(samples=100))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"


def test_more_than_two_percent_core_regression_is_no_go():
    payload = _payload()
    for name in CORE_METRICS:
        _set(payload, "full", name, 0.78)
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "FULL_CORE_REGRESSION" for f in report["findings"])


def test_context_pollution_isolated_as_context_regression():
    payload = _payload()
    for name in CORE_METRICS:
        _set(payload, "context", name, 0.77)
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "CONTEXT_CORE_REGRESSION" for f in report["findings"])


def test_single_metric_regression_cannot_hide_inside_the_core_mean():
    payload = _payload()
    _set(payload, "full", "reasoning_accuracy", 0.70)       # -12.5% on reasoning
    for name in ("coding_correctness", "structured_output_accuracy", "unknown_task_adaptation"):
        _set(payload, "full", name, 0.84)                     # +5% elsewhere: mean is 0.805 >= 0.8
    report = evaluate(payload)
    assert report["scores"]["full_core_retention"] > 0.98
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "CORE_METRIC_REGRESSION" and "reasoning_accuracy" in f["message"] for f in report["findings"])


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


def test_zero_raw_hallucination_must_stay_zero():
    payload = _payload()
    payload["modes"]["raw"]["hallucination_rate"]["score"] = 0.0
    payload["modes"]["full"]["hallucination_rate"]["score"] = 0.01
    report = evaluate(payload)
    assert report["status"] == "NO_GO"
    assert any(f["code"] == "HALLUCINATION_REGRESSION" for f in report["findings"])


def test_secondary_metric_drop_is_reported_not_hidden():
    payload = _payload()
    payload["modes"]["full"]["memory_retrieval_accuracy"]["score"] = 0.70
    report = evaluate(payload)
    assert report["status"] == "PASS"                         # not a release blocker by policy...
    assert any(f["code"] == "SECONDARY_REGRESSION" and "memory_retrieval" in f["message"]
               for f in report["findings"])                   # ...but never silent
    assert report["scores"]["secondary_retention"]["memory_retrieval_accuracy"] == pytest.approx(0.875)


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
    for mode in payload["modes"].values():
        mode["reasoning_accuracy"]["samples"] = 3
    with pytest.raises(ValueError, match="insufficient samples"):
        evaluate(payload, GateConfig(min_samples_per_metric=20))


def test_unpaired_item_counts_across_lanes_are_not_the_same_experiment():
    payload = _payload()
    payload["modes"]["full"]["reasoning_accuracy"]["samples"] = 999
    with pytest.raises(ValueError, match="unpaired items"):
        evaluate(payload)


@pytest.mark.parametrize("field", ["model", "dataset_id", "evaluated_sha"])
def test_missing_identity_is_insufficient(field):
    payload = _payload()
    del payload[field]
    with pytest.raises(ValueError, match=field.split("_")[0]):
        evaluate(payload)


def test_lane_measured_on_a_different_model_or_dataset_is_refused():
    payload = _payload()
    payload["modes"]["full"]["model"] = "other-model"
    with pytest.raises(ValueError, match="not the same experiment"):
        evaluate(payload)
    payload = _payload()
    payload["modes"]["context"]["dataset_id"] = "different-items"
    with pytest.raises(ValueError, match="not the same experiment"):
        evaluate(payload)
    payload = _payload()
    payload["quantization"] = "fp16"
    payload["modes"]["full"]["quantization"] = "q4"
    with pytest.raises(ValueError, match="model configuration"):
        evaluate(payload)


def test_evidence_from_another_commit_does_not_transfer():
    payload = _payload()
    with pytest.raises(ValueError, match="OLD_SHA_PASS"):
        evaluate(payload, GateConfig(expect_sha="d" * 40))
    assert evaluate(payload, GateConfig(expect_sha=SHA.upper()))["status"] == "PASS"
    payload["evaluated_sha"] = "c" * 12
    with pytest.raises(ValueError, match="evaluated_sha"):
        evaluate(payload)


def test_paired_counts_that_contradict_the_score_are_refused():
    payload = _payload()
    payload["modes"]["full"]["reasoning_accuracy"]["score"] = 0.70    # paired says lost=0
    with pytest.raises(ValueError, match="contradict"):
        evaluate(payload)


def test_paired_loss_bound_is_conservative():
    """Lost 12 of 1000 on every core metric, nothing gained: point retention 0.985
    clears 0.98, but the upper bound on the loss rate (~2.1%) does not, so the
    claim is INSUFFICIENT, not PASS. Losing 5 of 1000 on one metric, by contrast,
    is provably within the margin and passes."""
    payload = _payload()
    for name in CORE_METRICS:
        payload["modes"]["full"][name] = {"score": 0.788, "samples": 1000, "paired": {"lost": 12, "gained": 0}}
    report = evaluate(payload)
    assert report["scores"]["full_core_retention"] == pytest.approx(0.985)
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["scores"]["core_retention_lower_bound"]["full"] < 0.98
    small = _payload()
    small["modes"]["full"]["reasoning_accuracy"] = {"score": 0.795, "samples": 1000, "paired": {"lost": 5, "gained": 0}}
    assert evaluate(small)["status"] == "PASS"
    lo, hi = wilson(5, 1000, 1.959963984540054)
    assert 0.0 < lo < 0.005 < hi < 0.013


def test_wilson_interval_basics():
    lo, hi = wilson(0, 200, 1.959963984540054)
    assert lo == 0.0 and 0.018 < hi < 0.02
    lo, hi = wilson(800, 1000, 1.959963984540054)
    assert 0.77 < lo < 0.8 < hi < 0.83


def test_cli_binds_the_file_to_the_commit_under_test(tmp_path):
    tool = Path(__file__).resolve().parents[1] / "tools" / "intelligence_preservation_gate.py"
    src = tmp_path / "ip.json"
    src.write_text(json.dumps(_payload()), encoding="utf-8")
    ok = subprocess.run([sys.executable, str(tool), str(src), "--expect-sha", SHA], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout
    stale = subprocess.run([sys.executable, str(tool), str(src), "--expect-sha", "e" * 40], capture_output=True, text=True)
    assert stale.returncode == 2 and "OLD_SHA_PASS" in stale.stdout
    src.write_text(json.dumps(_payload(paired=False)), encoding="utf-8")
    weak = subprocess.run([sys.executable, str(tool), str(src)], capture_output=True, text=True)
    assert weak.returncode == 2 and "INSUFFICIENT_EVIDENCE" in weak.stdout


# ------------------------------------------------------ the FULL lane must be real

def test_a_prompt_ablation_cannot_stand_in_for_the_full_lane():
    """AF-04, now enforced by the gate and not only by the runner.

    A FULL lane that appends a list of tool names to the prompt and calls the
    model once measures nothing about whether Bossman's own loop preserves
    quality — and it would answer with a number that cannot be wrong. Two
    runners in this repository can write this file; only one of them runs the
    harness, and the gate must be able to tell them apart.
    """
    payload = _payload()
    payload["lanes"]["full"] = {"lane": "full", "kind": "prompt_ablation",
                                "executes_tools": False}
    with pytest.raises(ValueError, match="did not run the harness"):
        evaluate(payload)


def test_evidence_that_does_not_say_how_it_was_produced_is_refused():
    payload = _payload()
    payload.pop("lanes")
    with pytest.raises(ValueError, match="missing lanes block"):
        evaluate(payload)

    partial = _payload()
    partial["lanes"].pop("full")
    with pytest.raises(ValueError, match="missing lanes.full"):
        evaluate(partial)


def test_a_full_lane_that_executed_nothing_is_refused():
    """Declaring `executes_tools` is a claim; `observed.executed` is the number."""
    payload = _payload()
    payload["lanes"]["full"]["observed"]["executed"] = 0
    with pytest.raises(ValueError, match="no tool actually executed"):
        evaluate(payload)

    missing = _payload()
    missing["lanes"]["full"].pop("observed")
    with pytest.raises(ValueError, match="missing lanes.full.observed"):
        evaluate(missing)

    lying = _payload()
    lying["lanes"]["full"]["observed"]["executed"] = True   # bool is not a count
    with pytest.raises(ValueError, match="no tool actually executed"):
        evaluate(lying)


def test_the_real_full_lane_still_passes():
    """Negative control for all three refusals above.

    Without this, the checks could be tightened until nothing passes and the
    suite would still look green.
    """
    assert evaluate(_payload())["status"] == "PASS"
