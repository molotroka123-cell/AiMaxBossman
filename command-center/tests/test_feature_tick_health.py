"""The sweep that keeps INV-RD-1 has to be watchable.

`waiting_approval` may not outlive its decision — and what makes that true at
runtime is a background sweep living in the `review_gate` feature tick. That
loop swallows its own exceptions so one bad tick cannot kill it, which is
right, and it reported nothing, which was not: a tick that died or threw on
every pass looked exactly like a tick doing its job, and "a task cannot wait
forever" quietly became a promise with no observer.

`/api/health` now carries every feature loop beside the worker, scheduler and
metrics loops, under the same rules. These tests are mostly about the ways it
must NOT read green.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from bcc.api import Services, _health
from bcc.features import Feature


@pytest.fixture
def svc(tmp_path):
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "data",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data' / 'h.db'}",
                        ui_dir=tmp_path / "no-ui")
    return Services(settings, start_workers=False, announce_token=False)


# ------------------------------------------------------- the loop is listed

def test_every_ticking_feature_is_registered_before_it_ever_runs(svc):
    """A feature that has not ticked yet must be visible as such. Appearing
    only after the first success is the same blindness in a smaller window."""
    ticking = {f.name for f in svc.features if f.tick and f.tick_seconds > 0}
    assert ticking, "фич с тиком нет — тогда и свипа INV-RD-1 нет"
    assert set(svc.feature_ticks) == ticking
    assert all(state["at"] == 0.0 for state in svc.feature_ticks.values())


def test_the_review_gate_sweep_is_one_of_them(svc):
    """Named explicitly: this is the loop the P0 fix depends on."""
    assert "review_gate" in svc.feature_ticks
    assert svc.feature_ticks["review_gate"]["every"] > 0


async def test_health_reports_a_line_per_feature_loop(svc):
    health = await _health(svc)
    for name in svc.feature_ticks:
        assert f"tick:{name}" in health, name


# ------------------------------------------------------ it must not lie green

async def test_a_loop_that_never_ticked_is_not_ok(svc):
    svc.start_workers = True
    health = await _health(svc)
    assert health["tick:review_gate"]["status"] == "starting"


async def test_a_loop_that_stopped_ticking_goes_stale(svc):
    svc.start_workers = True
    state = svc.feature_ticks["review_gate"]
    state["at"] = time.monotonic() - (state["every"] * 3 + 60)
    health = await _health(svc)
    assert health["tick:review_gate"]["status"] == "stale"


async def test_a_loop_that_throws_every_pass_is_an_error_not_a_tick(svc):
    """The exact failure this exists to surface: the loop is alive, it runs on
    schedule, and it accomplishes nothing."""
    svc.start_workers = True
    svc.feature_ticks["review_gate"].update(
        {"at": time.monotonic(), "error": "OperationalError: no such table: approvals"})
    health = await _health(svc)
    assert health["tick:review_gate"]["status"] == "error"
    assert "no such table" in health["tick:review_gate"]["detail"]


async def test_a_recent_successful_tick_is_ok(svc):
    svc.start_workers = True
    svc.feature_ticks["review_gate"].update({"at": time.monotonic(), "error": None})
    assert (await _health(svc))["tick:review_gate"]["status"] == "ok"


async def test_with_workers_off_the_loops_say_stopped_not_ok(svc):
    """A harness that never starts the loops must not read as healthy."""
    svc.feature_ticks["review_gate"].update({"at": 0.0, "error": "boom"})
    assert (await _health(svc))["tick:review_gate"]["status"] == "stopped"


# --------------------------------------------------- the loop keeps its shape

async def test_only_a_successful_pass_advances_the_clock(svc):
    """A throwing tick must not stamp a fresh timestamp — that would turn a
    dead sweep into a permanently healthy one."""
    calls = {"n": 0}

    async def tick(_svc):
        calls["n"] += 1
        if calls["n"] == 1:
            return
        raise RuntimeError("сломалось")

    feature = Feature(name="probe", router=None, tick=tick, tick_seconds=0.01)
    svc.features = list(svc.features) + [feature]
    task = asyncio.create_task(svc._feature_tick(feature))
    try:
        while calls["n"] < 3:
            await asyncio.sleep(0.01)
    finally:
        svc._stopping.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    state = svc.feature_ticks["probe"]
    assert state["error"] and "сломалось" in state["error"]
    assert state["at"] > 0.0, "первый успешный тик должен был отметиться"
    svc.start_workers = True
    assert (await _health(svc))["tick:probe"]["status"] == "error"


async def test_a_throwing_tick_does_not_kill_the_loop(svc):
    """Unchanged behaviour, asserted because the new bookkeeping runs inside
    the same except block."""
    calls = {"n": 0}

    async def tick(_svc):
        calls["n"] += 1
        raise RuntimeError("каждый раз")

    feature = Feature(name="probe2", router=None, tick=tick, tick_seconds=0.01)
    task = asyncio.create_task(svc._feature_tick(feature))
    try:
        while calls["n"] < 3:
            await asyncio.sleep(0.01)
    finally:
        svc._stopping.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert calls["n"] >= 3
    assert svc.feature_ticks["probe2"]["at"] == 0.0
