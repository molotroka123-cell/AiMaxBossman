"""Synchronous fixture events only; no desktop/human-latency certification."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bossman_v3.contracts import TypedAction
from bossman_v3.visual_state.models import StateFragment, StateIdentity
from bossman_v3.visual_state.reaction import ReactionCoordinator, ReactionStateError

BASE = datetime(2026, 9, 5, tzinfo=timezone.utc)
ID = StateIdentity("editor", "window-1", "doc-1", 1, 1)
ACTION = TypedAction("editor.click", {"element": "save", "position": {"x": 10}})
STATE = {"element": "save", "enabled": True, "focused": True, "modal": None}


class Clock:
    value = 10.0
    def __call__(self):
        return self.value


@pytest.fixture
def env():
    clock = Clock()
    return clock, ReactionCoordinator(enabled=True, clock=clock)


def now(clock):
    return BASE + timedelta(seconds=clock.value)


def frame(clock, *, identity=ID, payload=None):
    return StateFragment("a11y", now(clock), STATE if payload is None else payload,
                         "trusted-fixture", identity=identity)


def observe(env, sequence=1, *, identity=ID, payload=None):
    clock, reaction = env
    return reaction.observe([frame(clock, identity=identity, payload=payload)], capture_sequence=sequence,
                            captured_monotonic=clock.value, now=now(clock))


def queue(env, timeout=3):
    clock, reaction = env
    reaction.queue_action(ACTION, required_state=STATE, timeout_seconds=timeout, now=now(clock))


def test_disabled_by_default_cannot_observe_queue_or_offer_dispatch():
    clock = Clock()
    reaction = ReactionCoordinator(clock=clock)
    assert not reaction.observe([frame(clock)], capture_sequence=1, captured_monotonic=10, now=now(clock))
    with pytest.raises(ReactionStateError):
        reaction.queue_action(ACTION, required_state=STATE, timeout_seconds=1, now=now(clock))
    result = reaction.poll(now=now(clock))
    assert result.reason == "disabled"
    assert not result.ready_for_canonical_gate and not result.dispatch_authorized


def test_current_action_is_frozen_and_single_use_advice(env):
    clock, reaction = env
    observe(env)
    args = {"element": "save", "position": {"x": 10}}
    action = replace(ACTION, args=args)
    reaction.queue_action(action, required_state=STATE, timeout_seconds=3, now=now(clock))
    args["position"]["x"] = 999
    result = reaction.poll(now=now(clock))
    assert result.ready_for_canonical_gate and not result.dispatch_authorized
    assert result.action.args["position"]["x"] == 10
    with pytest.raises(TypeError):
        result.action.args["position"]["x"] = 999
    assert not reaction.poll(now=now(clock)).ready_for_canonical_gate


@pytest.mark.parametrize("identity", [replace(ID, window_id="window-2"),
    replace(ID, document_id="doc-2"), replace(ID, navigation_generation=2), replace(ID, state_revision=2)])
def test_identity_change_invalidates_queued_action(env, identity):
    clock, reaction = env
    observe(env)
    queue(env)
    clock.value += .1
    observe(env, 2, identity=identity)
    assert reaction.poll(now=now(clock)).reason == "no_pending_action"


@pytest.mark.parametrize("change", [{"element": "delete"}, {"modal": "Confirm transfer"}, {"focused": False}])
def test_semantic_change_invalidates_even_if_identity_is_reused(env, change):
    clock, reaction = env
    observe(env)
    queue(env)
    clock.value += .1
    observe(env, 2, payload={**STATE, **change})
    assert not reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_out_of_order_event_cannot_restore_previous_window(env):
    clock, reaction = env
    observe(env, 2, identity=replace(ID, window_id="window-2"))
    queue(env)
    assert not observe(env, 1, identity=ID)
    assert reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_same_state_refresh_coalesces_without_extending_action_deadline(env):
    clock, reaction = env
    observe(env)
    queue(env, timeout=1)
    for seq in range(2, 6):
        clock.value += .25
        observe(env, seq)
    assert reaction.poll(now=now(clock)).reason == "deadline_expired"


def test_repeated_state_keeps_pending_action(env):
    clock, reaction = env
    observe(env)
    queue(env)
    clock.value += .1
    observe(env, 2)
    assert reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_interrupt_latches_and_resume_requires_new_post_resume_capture(env):
    clock, reaction = env
    observe(env)
    queue(env)
    reaction.interrupt()
    clock.value += .1
    assert not observe(env, 2)
    assert reaction.poll(now=now(clock)).reason == "owner_interrupted"
    reaction.resume()
    with pytest.raises(ReactionStateError):
        queue(env)
    with pytest.raises(ReactionStateError, match="pre-resume"):
        reaction.observe([frame(clock)], capture_sequence=3, captured_monotonic=10, now=now(clock))
    clock.value += .1
    observe(env, 4)
    queue(env)
    assert reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_interrupt_resume_during_iteration_cannot_cache_pre_resume_batch(env):
    clock, reaction = env
    def events():
        reaction.interrupt()
        reaction.resume()
        yield frame(clock)
    assert not reaction.observe(events(), capture_sequence=1, captured_monotonic=10, now=now(clock))
    with pytest.raises(ReactionStateError):
        queue(env)


def test_wall_clock_freeze_cannot_extend_observation_lifetime(env):
    clock, reaction = env
    observe(env)
    queue(env, timeout=20)
    clock.value += 6
    assert reaction.poll(now=BASE + timedelta(seconds=10)).reason == "observation_expired"


def test_new_sequence_with_older_capture_invalidates_pending(env):
    clock, reaction = env
    observe(env)
    queue(env)
    with pytest.raises(ReactionStateError):
        reaction.observe([frame(clock)], capture_sequence=2, captured_monotonic=9, now=now(clock))
    assert not reaction.poll(now=now(clock)).ready_for_canonical_gate


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 9, -1])
def test_invalid_or_regressed_clock_discards_state_and_pending(env, value):
    clock, reaction = env
    observe(env)
    queue(env)
    clock.value = value
    with pytest.raises(ReactionStateError, match="clock"):
        reaction.poll(now=BASE)
    clock.value = 11
    assert not reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_delayed_capture_processing_expires_on_monotonic_clock(env):
    clock, reaction = env
    def delayed():
        clock.value += 6
        yield frame(clock)
    with pytest.raises(ReactionStateError, match="processing"):
        reaction.observe(delayed(), capture_sequence=1, captured_monotonic=10, now=now(clock)+timedelta(seconds=6))


def test_newer_capture_reentry_wins_over_outer_batch(env):
    clock, reaction = env
    def outer():
        observe(env, 2, identity=replace(ID, window_id="window-2"))
        yield frame(clock)
    assert not reaction.observe(outer(), capture_sequence=1, captured_monotonic=10, now=now(clock))
    queue(env)
    assert reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_one_pending_action_cannot_be_silently_overwritten(env):
    observe(env)
    queue(env)
    with pytest.raises(ReactionStateError, match="already pending"):
        queue(env)


def test_fragment_batch_bound_fails_closed(env):
    clock, reaction = env
    observe(env)
    queue(env)
    with pytest.raises(ReactionStateError, match="128"):
        reaction.observe([frame(clock)] * 129, capture_sequence=2, captured_monotonic=10, now=now(clock))
    assert not reaction.poll(now=now(clock)).ready_for_canonical_gate


def test_deadline_rechecked_after_semantic_processing(env, monkeypatch):
    clock, reaction = env
    observe(env)
    queue(env, timeout=1)
    real_check = reaction._guard.check
    def delayed_check(*args, **kwargs):
        real_check(*args, **kwargs)
        clock.value += 1
    monkeypatch.setattr(reaction._guard, "check", delayed_check)
    assert reaction.poll(now=now(clock)).reason == "deadline_expired"
