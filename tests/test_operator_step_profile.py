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
    # +2 in each: the COMPLETE turn's `before`, and the verdict observation
    # AT-01 takes so the postcondition is checked against a screen the planner
    # has not already read.
    assert with_reuse["observations"] == 14
    assert without["observations"] == 26
    assert with_reuse["observations_reused"] == 12 and without["observations_reused"] == 0
    # AT-03 re-reads the screen before every dispatched action. That is one
    # probe per verified step in both arms — the reuse window does not and must
    # not skip it, because what it saves is a stale `before`, not a stale
    # authorization to act.
    for arm in (with_reuse, without):
        assert arm["boundary_probes"] == 12
        assert arm["boundary_probes_per_verified_action"] == 1.0
        assert arm["stale_boundaries"] == 0 and arm["completions_refused"] == 0


OBSERVE_MS = 100
SAVED_OBSERVATIONS = 12
# Synthetic I/O cost chosen large enough that the signal stays well above a
# shared-runner scheduling wobble. It is NOT a claim about the owner's host.
PREDICTED_SAVING_MS = SAVED_OBSERVATIONS * OBSERVE_MS
# The gate asks the manager's measured OBSERVE phase to retain at least half of
# the structural saving. Measuring that phase directly avoids folding unrelated
# storage/scheduler noise into the verdict while still failing if reuse stops
# saving real observation work.
SAVING_FLOOR_MS = PREDICTED_SAVING_MS * 0.5


def fastest_wall_ms(attempts=3, **kw):
    """Minimum wall time helper retained for diagnostic/other tests.

    It is deliberately no longer the acceptance signal for observation reuse:
    whole-run wall time includes durable-store work and host scheduling that the
    reuse optimization does not control. The manager already records each phase
    directly, which is the narrower and stronger measurement for this contract.
    """
    return min(run(**kw)["wall_total_ms"] for _ in range(attempts))


def test_the_saving_scales_with_the_measured_observation_cost():
    """A declared observation cost produces fewer measured observation-phase
    milliseconds when the verified observation is reused.

    The structural count is checked first, then the manager's own phase timer is
    used for the time assertion. This keeps the gate about the optimization it
    names instead of making unrelated durable-store jitter decide the result.
    """
    with_reuse = run(steps=12, observe_ms=OBSERVE_MS, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    without = run(steps=12, observe_ms=OBSERVE_MS, plan_ms=0, act_ms=0, reuse_max_age_s=0.0)
    saved_calls = without["observations"] - with_reuse["observations"]
    assert saved_calls == SAVED_OBSERVATIONS
    assert (without["declared_total_ms"] - with_reuse["declared_total_ms"]
            == saved_calls * OBSERVE_MS)

    with_phase = with_reuse["phase_timing"]["observe"]
    without_phase = without["phase_timing"]["observe"]
    assert without_phase["count"] - with_phase["count"] == SAVED_OBSERVATIONS
    measured_saved_ms = without_phase["total_ms"] - with_phase["total_ms"]
    assert measured_saved_ms >= SAVING_FLOOR_MS, (
        f"reuse saved only {measured_saved_ms:.1f} measured observe-phase ms "
        f"for {saved_calls} omitted observations; expected at least {SAVING_FLOOR_MS:.1f} ms")


# Абсолютный потолок остаётся — но как признак «что-то сломано катастрофически»,
# а не как мерка железа. Шаг дороже четверти секунды не бывает ни на каком хосте,
# который вообще годится для работы оператора.
BROKEN_STEP_MS = 250
# Относительная мерка: во сколько САМЫХ ДЕШЁВЫХ долговечных записей этого хоста
# обходится шаг.
#
# Честно о том, что этот порог ловит, а что нет — измерено, а не прикинуто.
# База на рабочей машине: 11.0–12.7 «полов» на 20 шагах. С НАМЕРЕННО внесённой
# регрессией того самого класса, что назван в docstring (работа, пропорциональная
# длине истории, на каждом переходе состояния) — 14.2–15.9. Разделение есть, но
# оно узкое, и на общем раннере CI шум его закроет. Поэтому порог поставлен туда,
# где он означает «сломано структурно» (кратный рост), и НЕ претендует ловить
# регрессию в 20%. Выдавать 60 за чувствительный порог было бы неправдой.
MAX_FLOORS_PER_STEP = 60


def test_the_framework_adds_a_bounded_amount_on_top_of_the_declared_costs():
    """With every declared cost at zero the wall time IS the framework: the
    store writes, policy, verifier and loop guard. A regression that made the
    loop, say, re-serialise history per state transition would show up here.

    Судится это ДВУМЯ мерками, и абсолютная — не главная. Одно и то же число
    (5 мс на рабочей станции, 124 мс на общем раннере CI) — это одна и та же
    программа на разном железе, и абсолютный порог в такой паре меряет раннер,
    а не регрессию. Поэтому основной критерий — отношение к полу самого хоста,
    снятому ЧЕРЕДУЯСЬ с прогоном; абсолютный потолок остаётся вторым рубежом.
    Сырое число печатается всегда, и в отчёт оно попадает целиком: ни один
    замер здесь не прячется и не «нормируется» задним числом."""
    report = run(steps=20, observe_ms=0, plan_ms=0, act_ms=0, reuse_max_age_s=0.75)
    floors = report["framework_overhead_in_floors"]
    # Объединение двух реализаций одной гарантии. На этой линии печатались
    # накладные, пол хоста, число полов и p95; на линии управления (коммит
    # 755d7bb7) — p50, max, число шагов и сами замеры. Ни одна не надмножество
    # другой, а нужны обе половины: по «p95 = 71.3» нельзя отличить «каркас
    # поехал» от «один тяжёлый шаг из двадцати», а без пола хоста нельзя
    # отличить регрессию от медленного раннера.
    raw = (f"overhead={report['framework_overhead_per_step_ms']}ms "
           f"floor={report['host_storage_floor_ms']}ms floors={floors} "
           f"p50={report.get('p50_step_ms')}ms p95={report['p95_step_ms']}ms "
           f"max={report.get('max_step_ms')}ms шагов={report.get('steps')} "
           f"замеры={report.get('samples_ms')}")
    assert floors is not None, f"пол хоста не измерен: {raw}"
    assert floors < MAX_FLOORS_PER_STEP, raw
    assert report["framework_overhead_per_step_ms"] < BROKEN_STEP_MS, raw
    assert report["p95_step_ms"] < BROKEN_STEP_MS, raw


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


def test_phase_timing_is_measured_per_phase_and_never_below_the_declared_cost():
    """V6 §A: the manager measures each phase's wall time itself. A declared 20 ms
    observation cannot show up as less than 20 ms; phases that never ran are
    absent, not zero."""
    r = run(steps=6, observe_ms=20, plan_ms=10, act_ms=5, reuse_max_age_s=0.0)
    pt = r["phase_timing"]
    assert set(pt) >= {"observe", "plan", "act"}, pt
    assert pt["observe"]["count"] == r["observations"] - r["boundary_probes"] or pt["observe"]["count"] >= 6
    assert pt["observe"]["total_ms"] >= pt["observe"]["count"] * 20 * 0.95
    assert pt["plan"]["total_ms"] >= pt["plan"]["count"] * 10 * 0.95
    assert pt["act"]["count"] == r["steps"] and pt["act"]["total_ms"] >= r["steps"] * 5 * 0.95
    for row in pt.values():
        assert row["max_ms"] >= row["mean_ms"] and row["timeouts"] == 0 and row["errors"] == 0
