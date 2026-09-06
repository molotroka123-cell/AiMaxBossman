"""BUG-OPERATOR-CONTROL-001 and the observation-reuse window.

The operator loop used to decode a ``ComputerTask`` once per ``run()`` and keep
writing that snapshot back for the whole step. Every owner command
(``pause`` / ``stop`` / ``take_control``) goes through the same store, so a
command that landed while the loop was observing or acting was overwritten by
the loop's next ``_save`` — the desktop kept being driven after the owner had
stopped it. ``JsonTaskStore.save`` is now compare-and-set on
``ComputerTask.revision`` and the loop re-reads the authoritative row every
iteration.

The same loop took two full observations per step: one after the action and one
immediately afterwards as the next step's ``before``. On the owner's Windows
host each observation is a UIA descendant walk plus a full-screen PNG, so the
second one is the single most expensive avoidable item in the step. A verified
post-action observation IS the current state, so it is reused as the next
``before`` inside a bounded freshness window — and never after a generation
change, a verification failure or an expired window.
"""
import asyncio

import pytest

from bossman.computer_operator.manager import ComputerOperatorManager
from bossman.computer_operator.models import (ActionKind, ComputerAction, ComputerTask,
                                              ExpectedState, TaskState)
from bossman.computer_operator.store import JsonTaskStore, StaleTaskWrite
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def click(**kw):
    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ok"), **kw)


def complete():
    return ComputerAction.make(ActionKind.COMPLETE)


async def wait_for(cond, timeout_s=5.0, step=0.005):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


class GatedObserver(FakeObserver):
    """Blocks on the Nth ``observe`` call so a test can land an owner command
    exactly while the loop is observing."""

    def __init__(self, *, block_on_call, **kw):
        super().__init__(**kw)
        self.block_on_call = block_on_call
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def observe(self, *, generation):
        if len(self.generations) + 1 == self.block_on_call:
            self.entered.set()
            await self.gate.wait()
        return await super().observe(generation=generation)


# ---------------------------------------------------------------- store: CAS
def test_store_refuses_a_write_that_carries_an_older_revision(tmp_path):
    store = JsonTaskStore(tmp_path / "t.json")
    t = ComputerTask.create("goal")
    store.save(t)
    held = store.get(t.id)                       # the copy a long step would hold
    fresh = store.get(t.id)
    fresh.state = TaskState.PAUSED
    store.save(fresh)                            # the owner presses Pause
    held.state = TaskState.RUNNING
    with pytest.raises(StaleTaskWrite):
        store.save(held)
    assert store.get(t.id).state is TaskState.PAUSED


def test_revision_survives_a_restart_of_the_store(tmp_path):
    path = tmp_path / "t.json"
    t = ComputerTask.create("goal")
    JsonTaskStore(path).save(t)
    reopened = JsonTaskStore(path).get(t.id)
    assert reopened.revision == 1
    stale = ComputerTask.create("goal")
    stale.id = t.id
    stale.revision = 0
    with pytest.raises(StaleTaskWrite):
        JsonTaskStore(path).save(stale)


# ------------------------------------------------- owner commands are not lost
async def test_pause_during_the_post_action_observation_stops_the_desktop(tmp_path):
    """The regression itself: Pause landed while the loop was observing.

    Before the fix the loop's next ``_save`` restored OBSERVING over PAUSED and
    the second click was executed anyway.
    """
    observer = GatedObserver(block_on_call=2, summary="ok")
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), click(), complete()]),
                       observer, adapter=adapter)
    t = mgr.create_task("two clicks")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(observer.entered.is_set)
    mgr.pause(t.id)
    observer.gate.set()
    state = await asyncio.wait_for(run, 5)
    assert state is TaskState.PAUSED
    assert mgr.store.get(t.id).state is TaskState.PAUSED
    assert len(adapter.executed) == 1                    # the second click never ran


async def test_stop_during_the_post_action_observation_is_final(tmp_path):
    observer = GatedObserver(block_on_call=2, summary="ok")
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), click(), complete()]),
                       observer, adapter=adapter)
    t = mgr.create_task("two clicks")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(observer.entered.is_set)
    mgr.stop(t.id)
    observer.gate.set()
    state = await asyncio.wait_for(run, 5)
    assert state is TaskState.CANCELLED
    assert mgr.store.get(t.id).state is TaskState.CANCELLED
    assert len(adapter.executed) == 1
    assert mgr.control_lease.holder() is None


async def test_a_paused_task_resumes_and_reobserves_under_a_new_generation(tmp_path):
    observer = GatedObserver(block_on_call=2, summary="ok")
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), click(), complete()]),
                       observer, adapter=FakeAdapter())
    t = mgr.create_task("two clicks")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(observer.entered.is_set)
    mgr.pause(t.id)
    observer.gate.set()
    assert await asyncio.wait_for(run, 5) is TaskState.PAUSED
    resumed = mgr.resume(t.id)
    assert resumed.state is TaskState.RECOVERING
    before = len(observer.generations)
    assert await asyncio.wait_for(mgr.run(t.id), 5) is TaskState.COMPLETED
    # The resumed attempt observes again under the bumped generation: a
    # pre-interruption observation is never reused across an owner intervention.
    assert observer.generations[before] == resumed.generation


# ------------------------------------------------------- observation reuse
def _reuse_manager(tmp_path, observer, *, max_age):
    return make_manager(tmp_path / "t.json", FakePlanner([click(), click(), complete()]),
                        observer, adapter=FakeAdapter(), observation_reuse_max_age_s=max_age)


async def test_verified_observation_is_reused_as_the_next_step_before(tmp_path):
    observer = FakeObserver(summary="ok")
    mgr = _reuse_manager(tmp_path, observer, max_age=5.0)
    t = mgr.create_task("two clicks")
    assert await asyncio.wait_for(mgr.run(t.id), 5) is TaskState.COMPLETED
    # 1 initial + 1 after each of the two clicks. Without reuse the loop takes a
    # fresh "before" for step 2 and for the COMPLETE turn as well: 5 observations.
    assert len(observer.generations) == 3
    assert mgr.observations_taken == 3 and mgr.observations_reused == 2


async def test_reuse_off_takes_a_fresh_observation_for_every_step(tmp_path):
    observer = FakeObserver(summary="ok")
    mgr = _reuse_manager(tmp_path, observer, max_age=0)
    t = mgr.create_task("two clicks")
    assert await asyncio.wait_for(mgr.run(t.id), 5) is TaskState.COMPLETED
    assert len(observer.generations) == 5
    assert mgr.observations_reused == 0


async def test_an_expired_observation_is_never_reused(tmp_path):
    observer = FakeObserver(summary="ok")
    mgr = _reuse_manager(tmp_path, observer, max_age=1e-9)
    t = mgr.create_task("two clicks")
    assert await asyncio.wait_for(mgr.run(t.id), 5) is TaskState.COMPLETED
    assert len(observer.generations) == 5
    assert mgr.observations_reused == 0


async def test_a_failed_verification_forces_a_fresh_observation(tmp_path):
    """An unverified post-state is not evidence of anything; the next turn must
    look again rather than plan against an unconfirmed screen."""
    observer = FakeObserver(summary="not what was expected")
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]),
                       observer, adapter=FakeAdapter(), observation_reuse_max_age_s=5.0)
    t = mgr.create_task("one click")
    assert await asyncio.wait_for(mgr.run(t.id), 5) is TaskState.COMPLETED
    assert mgr.observations_reused == 0
    assert len(observer.generations) == 3        # before, after (failed), fresh before


async def test_reuse_cannot_cross_an_owner_intervention(tmp_path):
    """Generation is the interruption boundary: an observation captured before
    the owner touched the desktop can never become the next step's ``before``."""
    observer = FakeObserver(summary="ok")
    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "t.json"), planner=FakePlanner([]), observer=observer,
        action_router=None, approval_create=None, approval_wait=None, event_emit=lambda *a, **k: None,
        observation_reuse_max_age_s=5.0)
    t = mgr.create_task("x")
    obs = await observer.observe(generation=t.generation)
    import time as _t
    assert mgr._reuse((obs, t.generation, _t.monotonic()), t) is obs
    t.generation += 1
    assert mgr._reuse((obs, t.generation - 1, _t.monotonic()), t) is None


# ------------------------------------- an owner command is not a system failure
async def test_an_owner_state_is_not_relabelled_as_a_system_failure(tmp_path):
    """The loop blocks the step either way; only the recorded verdict changes.

    Before, any block after an owner command wrote FAILED with a technical
    reason ("stale observation: generation changed"), so Pause and Take control
    produced a task the owner could not resume.
    """
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click()]), FakeObserver(summary="ok"),
                       adapter=FakeAdapter())
    for command, expected in (("pause", TaskState.PAUSED),
                              ("take_control", TaskState.USER_CONTROL),
                              ("stop", TaskState.CANCELLED)):
        t = mgr.create_task(f"task {command}")
        getattr(mgr, command)(t.id)
        assert mgr._fail(mgr.store.get(t.id), "stale observation: generation changed") is expected
        stored = mgr.store.get(t.id)
        assert stored.state is expected
        assert stored.last_error == "stale observation: generation changed"
        assert stored.pending_action is None


async def test_emergency_lock_overrides_even_an_owner_paused_task(tmp_path):
    """The one command that dominates the others: the big red button still wins."""
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click()]), FakeObserver(summary="ok"),
                       adapter=FakeAdapter())
    t = mgr.create_task("paused then locked")
    mgr.pause(t.id)
    mgr.emergency_lock()
    assert mgr.store.get(t.id).state is TaskState.LOCKED
    assert mgr.control_lease.holder() is None


# --------------------------------------------------- store: bookkeeping cost
def test_the_store_does_not_reparse_the_whole_journal_on_every_save(tmp_path, monkeypatch):
    """The loop saves several times per step and the step history grows, so
    re-reading and re-parsing the journal on every save made bookkeeping
    quadratic in step count. Reads now come from a cache validated against the
    file's own stat."""
    import json as _json
    store = JsonTaskStore(tmp_path / "t.json")
    t = ComputerTask.create("goal")
    store.save(t)
    loads = []
    real = _json.loads
    monkeypatch.setattr("bossman.computer_operator.store.json.loads",
                        lambda *a, **k: (loads.append(1), real(*a, **k))[1])
    for _ in range(20):
        store.save(t)
        store.get(t.id)
    assert loads == [], "the journal was re-parsed despite no external change"


def test_a_write_by_another_holder_invalidates_the_cache(tmp_path):
    """The cache is only ever valid for the file state this instance wrote. A
    second store on the same path is what a restarted process looks like."""
    path = tmp_path / "t.json"
    a, b = JsonTaskStore(path), JsonTaskStore(path)
    t = ComputerTask.create("goal")
    a.save(t)
    assert b.get(t.id).state is TaskState.QUEUED       # b caches this file state
    fresh = a.get(t.id)
    fresh.state = TaskState.PAUSED
    a.save(fresh)
    assert b.get(t.id).state is TaskState.PAUSED, "b served a stale cached row"
    stale = b.get(t.id)
    stale.revision -= 1
    with pytest.raises(StaleTaskWrite):
        b.save(stale)


def test_a_journal_deleted_underneath_the_store_is_not_served_from_cache(tmp_path):
    path = tmp_path / "t.json"
    store = JsonTaskStore(path)
    t = ComputerTask.create("goal")
    store.save(t)
    assert store.get(t.id) is not None
    path.unlink()
    assert store.get(t.id) is None and store.list() == []


# ---------------------------------------- stop acknowledgement and dispatch block
class SlowObserver(FakeObserver):
    """An observation that takes as long as a heavy UIA walk on a real window."""

    def __init__(self, *, delay, **kw):
        super().__init__(**kw)
        self.delay = delay
        self.entered = asyncio.Event()
        self.cancelled = 0

    async def observe(self, *, generation):
        self.entered.set()
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return await super().observe(generation=generation)


class SlowPlanner(FakePlanner):
    """A planner turn that takes as long as real model inference."""

    def __init__(self, actions, *, delay):
        super().__init__(actions)
        self.delay = delay
        self.entered = asyncio.Event()
        self.cancelled = 0

    async def next_action(self, **kw):
        self.entered.set()
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return await super().next_action(**kw)


async def _stop_latency(mgr, task_id, command="stop"):
    started = asyncio.get_running_loop().time()
    getattr(mgr, command)(task_id)
    return started


async def test_stop_is_acknowledged_without_waiting_for_the_observation(tmp_path):
    """Owner-stop acknowledgement used to cost whatever the in-flight read cost.

    A UIA descendant walk on a heavy window and a model turn are seconds, so a
    Stop pressed at the wrong moment felt like the machine ignoring the owner.
    The latch is set synchronously by stop() and abandons the read.
    """
    observer = SlowObserver(delay=5.0, summary="ok")
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]), observer,
                       adapter=adapter)
    t = mgr.create_task("one click")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(observer.entered.is_set)
    started = await _stop_latency(mgr, t.id)
    state = await asyncio.wait_for(run, 5)
    elapsed = asyncio.get_running_loop().time() - started
    assert state is TaskState.CANCELLED
    assert observer.cancelled == 1, "the in-flight observation was not abandoned"
    assert adapter.executed == []
    # Engineering target for stop acknowledgement is 200 ms p95; the observation
    # this abandons is 5 s, so a regression to checkpoint-only stop cannot pass.
    assert elapsed < 0.2, f"stop acknowledged in {elapsed * 1000:.1f} ms"


async def test_stop_during_model_planning_prevents_the_dispatch(tmp_path):
    """No new input is dispatched after the owner's cancellation is observed."""
    planner = SlowPlanner([click(), complete()], delay=5.0)
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json", planner, FakeObserver(summary="ok"), adapter=adapter)
    t = mgr.create_task("one click")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(planner.entered.is_set)
    started = await _stop_latency(mgr, t.id)
    state = await asyncio.wait_for(run, 5)
    elapsed = asyncio.get_running_loop().time() - started
    assert state is TaskState.CANCELLED
    assert planner.cancelled == 1
    assert adapter.executed == [], "an action was dispatched after the owner stopped the task"
    assert elapsed < 0.25, f"dispatch prevented in {elapsed * 1000:.1f} ms"


async def test_pause_is_acknowledged_promptly_and_stays_resumable(tmp_path):
    observer = SlowObserver(delay=5.0, summary="ok")
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]), observer,
                       adapter=FakeAdapter())
    t = mgr.create_task("one click")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(observer.entered.is_set)
    started = await _stop_latency(mgr, t.id, "pause")
    state = await asyncio.wait_for(run, 5)
    assert asyncio.get_running_loop().time() - started < 0.2
    assert state is TaskState.PAUSED
    assert mgr.resume(t.id).state is TaskState.RECOVERING


async def test_an_interrupt_never_cancels_an_effect_that_is_already_running(tmp_path):
    """Cancelling a dispatch mid-flight would leave the outcome unknown.

    The effect completes and is still observed and verified; only the NEXT step
    is stopped. This is the boundary between a fast stop and an honest journal.
    """
    gate = asyncio.Event()
    adapter = FakeAdapter(gate=gate)
    observer = FakeObserver(summary="ok")
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), click(), complete()]),
                       observer, adapter=adapter)
    t = mgr.create_task("two clicks")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(adapter.entered.is_set)
    mgr.stop(t.id)
    gate.set()
    state = await asyncio.wait_for(run, 5)
    assert state is TaskState.CANCELLED
    assert len(adapter.executed) == 1, "the in-flight effect was abandoned instead of completed"
    stored = mgr.store.get(t.id)
    assert stored.history[0].after_observation_id, "the completed effect was left unobserved"
    assert len(adapter.executed) == 1                     # and no second dispatch


async def test_a_latch_with_no_command_behind_it_does_not_spin(tmp_path):
    """Defensive: a stale latch must not turn the loop into a busy wait."""
    observer = FakeObserver(summary="ok")
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]), observer,
                       adapter=FakeAdapter())
    t = mgr.create_task("one click")
    mgr._signal_interrupt(t.id)                            # latched, but nothing was written
    assert await asyncio.wait_for(mgr.run(t.id), 5) is TaskState.COMPLETED
    assert mgr.interrupts_observed >= 1
    assert not mgr._interrupt_pending(t.id)


async def test_emergency_lock_interrupts_every_running_task(tmp_path):
    observer = SlowObserver(delay=5.0, summary="ok")
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]), observer,
                       adapter=FakeAdapter())
    t = mgr.create_task("one click")
    run = asyncio.create_task(mgr.run(t.id))
    assert await wait_for(observer.entered.is_set)
    started = asyncio.get_running_loop().time()
    mgr.emergency_lock()
    state = await asyncio.wait_for(run, 5)
    assert asyncio.get_running_loop().time() - started < 0.2
    assert state is TaskState.LOCKED

