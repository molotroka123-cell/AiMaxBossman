"""Analyzer, motion gate, classifier and hysteresis."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from ai_webcam_vision.crm.base import CrmContext, ProcedureProvenance
from ai_webcam_vision.errors import BaselineMissing
from ai_webcam_vision.pipeline.analysis import Analyzer, BaselineStore, Evidence, frame_to_array
from ai_webcam_vision.pipeline.classifier import State, StateDebouncer, Thresholds, classify
from ai_webcam_vision.pipeline.motion import MotionGate
from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene


def evidence(room=0.0, chair=0.0, work=0.0, motion=False) -> Evidence:
    return Evidence(
        ts=datetime.now(timezone.utc),
        room_change=room,
        chair_change=chair,
        work_motion=work,
        motion_gate=motion,
        frame_seq=1,
    )


# ------------------------------------------------------------------ motion
def test_motion_gate_uses_an_injectable_clock():
    now = [100.0]
    gate = MotionGate(30, clock=lambda: now[0])
    assert gate.active() is False
    gate.trigger("onvif-bridge")
    assert gate.active() is True
    assert gate.state().source == "onvif-bridge"
    now[0] += 29
    assert gate.active() is True
    now[0] += 2
    assert gate.active() is False


def test_motion_gate_extends_on_new_motion():
    now = [0.0]
    gate = MotionGate(10, clock=lambda: now[0])
    gate.trigger()
    now[0] += 8
    gate.trigger()
    now[0] += 5
    assert gate.active() is True
    assert gate.state().triggers == 2


# ---------------------------------------------------------------- analysis
async def test_analyzer_scores_zones_against_baseline(tmp_path):
    store = BaselineStore(tmp_path / "baseline.npy")
    empty = SyntheticFrameSource(scene=SyntheticScene())
    store.save(await empty.grab())

    analyzer = Analyzer(store, (0.25, 0.25, 0.78, 0.90), (0.15, 0.10, 0.90, 0.95))
    quiet = await empty.grab()
    quiet_evidence = analyzer.analyze(quiet, motion_gate=False)
    assert quiet_evidence.room_change < 0.01
    assert quiet_evidence.chair_change < 0.01

    busy_source = SyntheticFrameSource(scene=SyntheticScene(chair_occupied=True, work_activity=True))
    busy_evidence = analyzer.analyze(await busy_source.grab(), motion_gate=True)
    assert busy_evidence.chair_change > 0.2
    assert busy_evidence.room_change > 0.05


async def test_first_frame_has_no_work_motion(tmp_path):
    store = BaselineStore(tmp_path / "baseline.npy")
    source = SyntheticFrameSource(scene=SyntheticScene())
    store.save(await source.grab())
    analyzer = Analyzer(store, (0.25, 0.25, 0.78, 0.90), (0.15, 0.10, 0.90, 0.95))
    first = analyzer.analyze(await source.grab(), motion_gate=False)
    assert first.work_motion == 0.0


def test_missing_baseline_is_reported(tmp_path):
    store = BaselineStore(tmp_path / "absent.npy")
    with pytest.raises(BaselineMissing):
        store.load()


async def test_baseline_file_is_owner_only(tmp_path):
    store = BaselineStore(tmp_path / "baseline.npy")
    source = SyntheticFrameSource()
    store.save(await source.grab())
    assert oct(store.path.stat().st_mode)[-3:] == "600"


async def test_frame_to_array_validates_payload_size():
    source = SyntheticFrameSource(width=160, height=90)
    frame = await source.grab()
    array = frame_to_array(frame)
    assert array.shape == (90, 160)
    assert array.dtype == np.uint8


# -------------------------------------------------------------- classifier
def test_movement_alone_is_not_work():
    result = classify(evidence(room=0.08, chair=0.001, work=0.05, motion=True), CrmContext())
    assert result.state is State.TRANSIT
    assert result.crm_available is False
    assert "crm_unavailable" in result.reasons


def test_clinical_work_requires_crm_agreement():
    busy = evidence(room=0.08, chair=0.10, work=0.05, motion=True)
    without_crm = classify(busy, CrmContext())
    assert without_crm.state is not State.CLINICAL_WORK

    with_crm = classify(
        busy,
        CrmContext(available=True, source="crm", appointment_active=True,
                   shift_active=True, planned_service="filling"),
    )
    assert with_crm.state is State.CLINICAL_WORK
    assert with_crm.procedure == "filling"
    assert with_crm.procedure_provenance == ProcedureProvenance.PLANNED.value


def test_confirmed_service_outranks_planned():
    context = CrmContext(available=True, appointment_active=True,
                         planned_service="planned", confirmed_service="confirmed")
    label, confidence, provenance = context.procedure()
    assert (label, confidence, provenance) == ("confirmed", 1.0, ProcedureProvenance.CONFIRMED)


def test_disabled_crm_never_supplies_a_procedure():
    label, confidence, provenance = CrmContext().procedure()
    assert label == "unknown" and confidence == 0.0
    assert provenance is ProcedureProvenance.UNKNOWN


def test_empty_room_and_prep_and_turnover_states():
    empty = classify(evidence(), CrmContext(available=True, source="crm"))
    assert empty.state is State.EMPTY

    prep = classify(
        evidence(room=0.09, chair=0.001, motion=True),
        CrmContext(available=True, appointment_active=True, shift_active=True),
    )
    assert prep.state is State.PREP

    turnover = classify(
        evidence(room=0.09, chair=0.09),
        CrmContext(available=True, shift_active=True, appointment_active=False),
    )
    assert turnover.state is State.TURNOVER


def test_staff_nonclinical_requires_shift_without_appointment():
    result = classify(
        evidence(room=0.09, chair=0.001, motion=True),
        CrmContext(available=True, shift_active=True, appointment_active=False),
    )
    assert result.state is State.STAFF_NONCLINICAL


def test_thresholds_are_configurable():
    strict = Thresholds(room=0.5, chair=0.5, work=0.5)
    result = classify(evidence(room=0.09, chair=0.09, work=0.09, motion=False), CrmContext(available=True), strict)
    assert result.state is State.EMPTY


# --------------------------------------------------------------- hysteresis
def test_debouncer_ignores_single_frame_flips():
    debouncer = StateDebouncer(samples=2, initial=State.EMPTY)
    assert debouncer.feed(State.CLINICAL_WORK) is State.EMPTY
    assert debouncer.feed(State.EMPTY) is State.EMPTY
    assert debouncer.feed(State.CLINICAL_WORK) is State.EMPTY
    assert debouncer.feed(State.CLINICAL_WORK) is State.CLINICAL_WORK
    assert debouncer.transitions == 1


def test_debouncer_rejects_bad_configuration():
    with pytest.raises(ValueError):
        StateDebouncer(samples=0)
