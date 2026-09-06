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


async def test_an_expired_observation_is_never_reused(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from bossman.computer_operator import manager as manager_module
    observer = FakeObserver(summary="ok")
    mgr = _reuse_manager(tmp_path, observer, max_age=5.0)
    task = mgr.create_task("two clicks")
    obs = await observer.observe(generation=task.generation)
    # Patch only this module's clock reference; never the asyncio timeout clock.
    now = [100.0]
    monkeypatch.setattr(manager_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    reusable = (obs, task.generation, now[0])
    assert mgr._reuse(reusable, task) is obs
    now[0] += 5.001
    assert mgr._reuse(reusable, task) is None
    assert mgr.observations_reused == 1


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
