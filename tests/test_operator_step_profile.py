"""What the desktop-operator loop costs per step — measured, not asserted.

These are properties of the framework only. The cost of a UIA walk, a
screenshot and a model turn belongs to the owner's host and model and is
supplied to the profiler, never invented here. Nothing in this file is evidence
of human-level computer use; see docs/testing/HUMAN_SPEED_AUDIT_20260906.md.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from operator_step_profile import profile  # noqa: E402


def run(**kw):
    return asyncio.run(profile(**kw))


def test_a_verified_step_costs_one_observation_not_two():
    """The regression this measures: the loop observed after the action and then
    immediately again as the next step's `before`. On the owner's Windows host
    that second observation is a UIA descendant walk plus a full-screen PNG."""
    with_reuse = run(steps=12, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    without = run(steps=12, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.0)
    # +1 in each: the final turn where the planner reports COMPLETE.
    assert with_reuse["observations"] == 13
    assert without["observations"] == 25
    assert with_reuse["observations_reused"] == 12 and without["observations_reused"] == 0


def test_the_saving_scales_with_the_measured_observation_cost():
    """A declared 100 ms observation is 12 fewer of them over 12 steps. The
    prediction is arithmetic over measured call counts, not a speed claim."""
    with_reuse = run(steps=12, observe_ms=20, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    without = run(steps=12, observe_ms=20, plan_ms=0, act_ms=0, reuse_max_age_s=0.0)
    saved_calls = without["observations"] - with_reuse["observations"]
    assert saved_calls == 12
    assert without["declared_total_ms"] - with_reuse["declared_total_ms"] == saved_calls * 20
    assert with_reuse["wall_total_ms"] < without["wall_total_ms"]


def test_the_framework_adds_a_bounded_amount_on_top_of_the_declared_costs():
    """With every declared cost at zero the wall time IS the framework: the
    store writes, policy, verifier and loop guard. A regression that made the
    loop, say, re-serialise history per state transition would show up here."""
    report = run(steps=20, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    # Печатается ВСЁ распределение, а не одно число: по «p95 = 71.3» нельзя
    # понять, поехал ли каркас или у раннера был один тяжёлый шаг из двадцати.
    why = (f"накладные={report['framework_overhead_per_step_ms']} мс "
           f"p50={report.get('p50_step_ms')} p95={report['p95_step_ms']} "
           f"max={report.get('max_step_ms')} шагов={report.get('steps')} "
           f"замеры={report.get('samples_ms')}")
    assert report["framework_overhead_per_step_ms"] < 40, why
    assert report["p95_step_ms"] < 60, why


def test_every_sample_is_retained_and_none_are_trimmed():
    report = run(steps=10, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    assert len(report["samples_ms"]) == report["steps"] == 10
    assert report["outliers_removed"] == 0
    assert report["max_step_ms"] >= report["p95_step_ms"] >= report["p50_step_ms"]


def test_the_report_never_claims_a_human_comparison():
    report = run(steps=3, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    assert report["human_comparison"] == "NOT_RUN"
    assert "framework_only" in report["scope"]


def test_a_run_that_did_not_finish_cleanly_is_an_error_not_a_number():
    """The profiler refuses to report timings for a run that stopped early: a
    partial run is not a faster run. 200 requested steps exceed the task's own
    max_steps, so the loop fails and the profiler must refuse, not divide the
    wall time by however many steps happened to land."""
    with pytest.raises(RuntimeError):
        run(steps=200, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    with pytest.raises(ValueError):
        run(steps=0, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)


@pytest.mark.timeout(300)
def test_the_cli_emits_valid_json_with_both_arms(tmp_path):
    out = tmp_path / "profile.json"
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "operator_step_profile.py"),
                           "--steps", "6", "--json-out", str(out)],
                          capture_output=True, text=True, timeout=280)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["observation_calls_saved"] == 6
    assert report["with_observation_reuse"]["steps"] == 6
    assert report["without_observation_reuse"]["observations_reused"] == 0
