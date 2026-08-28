"""Operational state from visual evidence plus CRM context.

Movement is not work. A state is only claimed when the CRM agrees, and when
the CRM is absent the classifier says so in the reasons instead of quietly
behaving as if there were no appointment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..crm.base import CrmContext
from .analysis import ANALYZER_ID, ANALYZER_VERSION, Evidence


class State(StrEnum):
    EMPTY = "EMPTY"
    TRANSIT = "TRANSIT"
    STAFF_NONCLINICAL = "STAFF_NONCLINICAL"
    PREP = "PREP"
    CLINICAL_WORK = "CLINICAL_WORK"
    TURNOVER = "TURNOVER"
    IDLE_OCCUPIED = "IDLE_OCCUPIED"
    UNKNOWN = "UNKNOWN"


OCCUPIED_STATES = (State.PREP, State.CLINICAL_WORK, State.IDLE_OCCUPIED, State.TURNOVER)


@dataclass(frozen=True)
class Thresholds:
    room: float = 0.035
    chair: float = 0.055
    work: float = 0.012


@dataclass(frozen=True)
class Classification:
    state: State
    confidence: float
    reasons: list[str] = field(default_factory=list)
    procedure: str = "unknown"
    procedure_confidence: float = 0.0
    procedure_provenance: str = "unknown"
    crm_available: bool = False
    crm_is_mock: bool = True
    analyzer: str = f"{ANALYZER_ID}:{ANALYZER_VERSION}"

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "procedure": {
                "label": self.procedure,
                "confidence": self.procedure_confidence,
                "provenance": self.procedure_provenance,
            },
            "crm": {"available": self.crm_available, "is_mock": self.crm_is_mock},
            "analyzer": self.analyzer,
        }


def classify(evidence: Evidence, crm: CrmContext, thresholds: Thresholds | None = None) -> Classification:
    t = thresholds or Thresholds()
    room = evidence.room_change >= t.room
    chair = evidence.chair_change >= t.chair
    work = evidence.work_motion >= t.work
    label, procedure_confidence, provenance = crm.procedure()

    reasons: list[str] = []
    if not crm.available:
        reasons.append("crm_unavailable")

    if not room and not chair and not evidence.motion_gate:
        state, confidence = State.EMPTY, 0.90
        reasons.append("frame_matches_empty_baseline")
    elif crm.available and crm.appointment_active and chair and work:
        state, confidence = State.CLINICAL_WORK, 0.90
        reasons += ["appointment_active", "chair_occupied", "work_zone_motion"]
    elif crm.available and crm.appointment_active and chair:
        state, confidence = State.IDLE_OCCUPIED, 0.74
        reasons += ["appointment_active", "chair_occupied", "no_work_motion"]
    elif crm.available and crm.appointment_active and room:
        state, confidence = State.PREP, 0.72
        reasons += ["appointment_active", "room_activity", "chair_near_baseline"]
    elif crm.available and not crm.appointment_active and chair:
        state, confidence = State.TURNOVER, 0.60
        reasons += ["no_active_appointment", "chair_not_at_baseline"]
    elif crm.available and crm.shift_active and room and not chair:
        state, confidence = State.STAFF_NONCLINICAL, 0.68
        reasons += ["shift_active", "room_activity", "chair_at_baseline"]
    elif (evidence.motion_gate or room) and not chair:
        state, confidence = State.TRANSIT, 0.65 if crm.available else 0.50
        reasons += ["movement_without_chair_evidence"]
    elif chair:
        # Chair evidence with no CRM corroboration is never called work.
        state, confidence = State.IDLE_OCCUPIED, 0.45
        reasons += ["chair_evidence_without_crm_confirmation"]
    else:
        state, confidence = State.UNKNOWN, 0.30
        reasons += ["evidence_below_all_thresholds"]

    return Classification(
        state=state,
        confidence=confidence,
        reasons=reasons,
        procedure=label,
        procedure_confidence=procedure_confidence,
        procedure_provenance=provenance.value,
        crm_available=crm.available,
        crm_is_mock=crm.is_mock,
    )


class StateDebouncer:
    """Hysteresis: a new state must repeat ``samples`` times before it wins.

    Without this a single noisy frame flips the room in and out of
    CLINICAL_WORK and the daily metrics become fiction.
    """

    def __init__(self, samples: int = 2, initial: State = State.UNKNOWN) -> None:
        if samples < 1:
            raise ValueError("samples must be >= 1")
        self.samples = samples
        self.current = initial
        self._candidate = initial
        self._streak = 0
        self.transitions = 0

    def feed(self, state: State) -> State:
        if state == self.current:
            self._candidate = state
            self._streak = 0
            return self.current
        if state == self._candidate:
            self._streak += 1
        else:
            self._candidate = state
            self._streak = 1
        if self._streak >= self.samples:
            self.current = state
            self._candidate = state
            self._streak = 0
            self.transitions += 1
        return self.current
