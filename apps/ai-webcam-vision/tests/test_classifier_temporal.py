"""The temporal classifier, attacked on purpose.

Counting samples is not hysteresis. At one sample per second, "two samples in
a row" is two seconds — long enough for somebody to cross the room, and short
enough for a noisy pair of frames to invent a procedure. Every test here is a
scenario that produced a wrong number before.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_webcam_vision.pipeline.classifier import (
    ALLOWED_TRANSITIONS,
    OCCUPIED_STATES,
    State,
    TemporalPolicy,
    TemporalStateMachine,
)

T0 = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def policy(**kwargs) -> TemporalPolicy:
    base = dict(
        min_samples=2,
        min_dwell_seconds=6.0,
        clinical_dwell_seconds=30.0,
        turnover_dwell_seconds=15.0,
        turnover_lookback_seconds=600.0,
        dropout_grace_seconds=45.0,
    )
    base.update(kwargs)
    return TemporalPolicy(**base)


def drive(machine: TemporalStateMachine, state: State, start: float, count: int, step: float = 1.0):
    last = machine.current
    for index in range(count):
        last = machine.feed(state, at(start + index * step))
    return last


# ------------------------------------------------------- short transit
def test_a_short_walk_through_the_room_never_becomes_clinical_work():
    """Somebody crosses the room for four seconds. That is not a procedure."""
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.CLINICAL_WORK, 0, 4)  # four seconds of chair + motion
    assert machine.current is State.EMPTY
    drive(machine, State.EMPTY, 4, 4)
    assert machine.current is State.EMPTY
    assert State.CLINICAL_WORK not in machine.committed_states


def test_a_real_procedure_is_recognised_once_it_lasts():
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.PREP, 0, 10)
    assert machine.current is State.PREP
    drive(machine, State.CLINICAL_WORK, 10, 40)
    assert machine.current is State.CLINICAL_WORK


def test_minimum_dwell_is_wall_clock_not_a_sample_count():
    """At 10 Hz, two samples is 0.2 s. Sample counting alone is not hysteresis."""
    machine = TemporalStateMachine(policy(min_samples=2), initial=State.EMPTY)
    drive(machine, State.TRANSIT, 0, 20, step=0.1)  # 20 samples, 2 seconds
    assert machine.current is State.EMPTY
    drive(machine, State.TRANSIT, 2.0, 60, step=0.1)  # now past 6 s of dwell
    assert machine.current is State.TRANSIT


# ------------------------------------------------------- detector dropout
def test_a_short_detector_dropout_does_not_split_one_procedure():
    """Frames stop for twenty seconds mid-procedure. It is still one procedure."""
    machine = TemporalStateMachine(policy(), initial=State.PREP)
    drive(machine, State.CLINICAL_WORK, 0, 40)
    assert machine.current is State.CLINICAL_WORK
    transitions_before = machine.transitions

    for second in range(20):
        assert machine.feed_dropout(at(40 + second)) is State.CLINICAL_WORK

    drive(machine, State.CLINICAL_WORK, 60, 40)
    assert machine.current is State.CLINICAL_WORK
    assert machine.transitions == transitions_before, "the procedure was cut in two"


def test_a_long_dropout_becomes_unknown_rather_than_a_pretended_state():
    machine = TemporalStateMachine(policy(dropout_grace_seconds=10.0), initial=State.PREP)
    drive(machine, State.CLINICAL_WORK, 0, 40)
    for second in range(20):
        machine.feed_dropout(at(40 + second))
    assert machine.current is State.UNKNOWN
    assert machine.dropouts >= 1


def test_a_dropout_does_not_reset_a_pending_candidate():
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.TRANSIT, 0, 4)          # candidate building, not yet due
    machine.feed_dropout(at(4))
    machine.feed_dropout(at(5))
    drive(machine, State.TRANSIT, 6, 4)          # dwell now satisfied overall
    assert machine.current is State.TRANSIT


# ------------------------------------------------------------- turnover
def test_turnover_needs_temporal_evidence_not_one_noisy_frame():
    """An empty room does not 'turn over'. Something has to have happened."""
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.TURNOVER, 0, 60)
    assert machine.current is not State.TURNOVER
    assert machine.rejected_transitions >= 1


def test_turnover_is_allowed_after_a_real_occupancy():
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.PREP, 0, 10)
    drive(machine, State.CLINICAL_WORK, 10, 40)
    assert machine.current is State.CLINICAL_WORK
    drive(machine, State.TURNOVER, 50, 20)
    assert machine.current is State.TURNOVER


def test_turnover_expires_when_the_occupancy_is_ancient():
    machine = TemporalStateMachine(policy(turnover_lookback_seconds=60.0), initial=State.EMPTY)
    drive(machine, State.PREP, 0, 10)
    drive(machine, State.CLINICAL_WORK, 10, 40)
    drive(machine, State.EMPTY, 50, 20)
    assert machine.current is State.EMPTY
    drive(machine, State.TURNOVER, 400, 30)
    assert machine.current is not State.TURNOVER


# --------------------------------------------------- impossible transitions
def test_the_transition_table_is_total_and_has_no_self_loops():
    for state in State:
        assert state in ALLOWED_TRANSITIONS, state
        assert state not in ALLOWED_TRANSITIONS[state], f"{state} lists itself"


def test_empty_never_jumps_straight_into_clinical_work():
    assert State.CLINICAL_WORK not in ALLOWED_TRANSITIONS[State.EMPTY]
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.CLINICAL_WORK, 0, 40)
    # The room had to become occupied first; one commit gets one legal step.
    assert machine.current is not State.EMPTY
    assert machine.current is not State.CLINICAL_WORK
    assert machine.current in OCCUPIED_STATES or machine.current is State.TRANSIT
    drive(machine, State.CLINICAL_WORK, 40, 60)
    assert machine.current is State.CLINICAL_WORK


def test_every_committed_transition_is_legal():
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    sequence = [
        State.TRANSIT, State.PREP, State.CLINICAL_WORK, State.IDLE_OCCUPIED,
        State.CLINICAL_WORK, State.TURNOVER, State.EMPTY, State.STAFF_NONCLINICAL,
    ]
    clock = 0.0
    for target in sequence:
        drive(machine, target, clock, 60)
        clock += 60
    for previous, following in zip(machine.history, machine.history[1:]):
        assert following in ALLOWED_TRANSITIONS[previous], f"{previous} -> {following}"


# ---------------------------------------------------------------- flapping
def test_alternating_evidence_never_commits_anything():
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    for index in range(100):
        target = State.CLINICAL_WORK if index % 2 else State.TRANSIT
        machine.feed(target, at(index))
    assert machine.current is State.EMPTY
    assert machine.transitions == 0


def test_a_single_noisy_frame_inside_a_procedure_changes_nothing():
    machine = TemporalStateMachine(policy(), initial=State.PREP)
    drive(machine, State.CLINICAL_WORK, 0, 40)
    machine.feed(State.EMPTY, at(41))
    machine.feed(State.CLINICAL_WORK, at(42))
    assert machine.current is State.CLINICAL_WORK
    assert machine.transitions == 1


def test_the_machine_reports_what_it_is_holding_back(settings=None):
    machine = TemporalStateMachine(policy(), initial=State.EMPTY)
    drive(machine, State.TRANSIT, 0, 3)
    snapshot = machine.to_dict()
    assert snapshot["current"] == State.EMPTY.value
    assert snapshot["pending"] == State.TRANSIT.value
    assert snapshot["pending_seconds"] == pytest.approx(2.0)
    assert snapshot["pending_samples"] == 3


def test_bad_policy_is_rejected():
    with pytest.raises(ValueError):
        TemporalPolicy(min_samples=0)
    with pytest.raises(ValueError):
        TemporalPolicy(min_dwell_seconds=-1.0)


# ---------------------------------------------------- wired into the service
async def test_the_service_uses_the_temporal_machine(settings):
    from ai_webcam_vision.runtime.service import VisionService
    from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True, chair_occupied=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await SyntheticFrameSource().grab())
        result = await service.sample_once()
        assert "temporal" in result
        assert result["temporal"]["current"] == result["debounced_state"]
        # Two fast samples cannot manufacture a state change.
        await service.sample_once()
        assert service.temporal.transitions == 0
    finally:
        await service.aclose()
