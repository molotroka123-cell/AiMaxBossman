"""Operational state from visual evidence plus CRM context.

Movement is not work. A state is only claimed when the CRM agrees, and when
the CRM is absent the classifier says so in the reasons instead of quietly
behaving as if there were no appointment.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

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
    if crm.stale:
        reasons.append("crm_stale")
    if crm.overlapping:
        reasons.append("crm_overlapping_appointments")

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

    if crm.stale:
        # Old intent is weaker evidence than current intent. The state still
        # stands — the camera saw what it saw — but the confidence must not
        # pretend the CRM half of the fusion is as good as it was.
        confidence *= 0.75

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


# ---------------------------------------------------------------------------
# The temporal layer
# ---------------------------------------------------------------------------

#: Which state may follow which. A room does not go from empty to a procedure
#: in one step: somebody has to walk in, and something has to be prepared.
#: Self-transitions are not listed because they are not transitions.
ALLOWED_TRANSITIONS: dict[State, frozenset[State]] = {
    State.EMPTY: frozenset({
        State.TRANSIT, State.STAFF_NONCLINICAL, State.PREP, State.UNKNOWN,
    }),
    State.TRANSIT: frozenset({
        State.EMPTY, State.STAFF_NONCLINICAL, State.PREP, State.IDLE_OCCUPIED,
        State.TURNOVER, State.UNKNOWN,
    }),
    State.STAFF_NONCLINICAL: frozenset({
        State.EMPTY, State.TRANSIT, State.PREP, State.TURNOVER,
        State.IDLE_OCCUPIED, State.UNKNOWN,
    }),
    State.PREP: frozenset({
        State.CLINICAL_WORK, State.IDLE_OCCUPIED, State.TRANSIT, State.TURNOVER,
        State.STAFF_NONCLINICAL, State.UNKNOWN,
    }),
    State.CLINICAL_WORK: frozenset({
        State.IDLE_OCCUPIED, State.PREP, State.TURNOVER, State.TRANSIT, State.UNKNOWN,
    }),
    State.IDLE_OCCUPIED: frozenset({
        State.CLINICAL_WORK, State.PREP, State.TURNOVER, State.TRANSIT,
        State.EMPTY, State.STAFF_NONCLINICAL, State.UNKNOWN,
    }),
    State.TURNOVER: frozenset({
        State.EMPTY, State.TRANSIT, State.PREP, State.STAFF_NONCLINICAL,
        State.IDLE_OCCUPIED, State.UNKNOWN,
    }),
    # UNKNOWN is where the machine sits when it has no evidence, so it must be
    # able to leave for anywhere the evidence points.
    State.UNKNOWN: frozenset(s for s in State if s is not State.UNKNOWN),
}

#: States that count as "somebody was in this room doing something".
_OCCUPANCY_EVIDENCE = frozenset({State.PREP, State.CLINICAL_WORK, State.IDLE_OCCUPIED})


@dataclass(frozen=True)
class TemporalPolicy:
    """How much evidence, over how much time, before a state is believed."""

    #: A candidate must be proposed at least this many times.
    min_samples: int = 2
    #: ...and for at least this many seconds of wall clock.
    min_dwell_seconds: float = 6.0
    #: Claiming clinical work is the expensive claim; it costs more evidence.
    clinical_dwell_seconds: float = 30.0
    #: Turnover is a claim about a room that has just been used.
    turnover_dwell_seconds: float = 15.0
    #: ...and only if that use was recent.
    turnover_lookback_seconds: float = 600.0
    #: A detector gap shorter than this holds the current state instead of
    #: cutting the timeline. Longer than this and the machine admits it does
    #: not know, rather than pretending the room stayed as it was.
    dropout_grace_seconds: float = 45.0

    def __post_init__(self) -> None:
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        for name in (
            "min_dwell_seconds",
            "clinical_dwell_seconds",
            "turnover_dwell_seconds",
            "turnover_lookback_seconds",
            "dropout_grace_seconds",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")

    def dwell_for(self, state: State) -> float:
        if state is State.CLINICAL_WORK:
            return max(self.clinical_dwell_seconds, self.min_dwell_seconds)
        if state is State.TURNOVER:
            return max(self.turnover_dwell_seconds, self.min_dwell_seconds)
        return self.min_dwell_seconds

    def to_dict(self) -> dict:
        return {
            "min_samples": self.min_samples,
            "min_dwell_seconds": self.min_dwell_seconds,
            "clinical_dwell_seconds": self.clinical_dwell_seconds,
            "turnover_dwell_seconds": self.turnover_dwell_seconds,
            "turnover_lookback_seconds": self.turnover_lookback_seconds,
            "dropout_grace_seconds": self.dropout_grace_seconds,
        }


class TemporalStateMachine:
    """Hysteresis, minimum dwell, dropout tolerance and legal transitions.

    Sample counting alone is not hysteresis: at 10 Hz two consecutive samples
    is a fifth of a second. Everything here is measured against the clock of
    the evidence, so the same policy behaves the same at any sampling rate.
    """

    def __init__(
        self,
        policy: TemporalPolicy | None = None,
        *,
        initial: State = State.UNKNOWN,
        history: int = 64,
    ) -> None:
        self.policy = policy or TemporalPolicy()
        self.current = initial
        self.transitions = 0
        self.rejected_transitions = 0
        self.dropouts = 0
        self.committed_states: set[State] = {initial}
        self.history: list[State] = [initial]
        self._history_limit = history
        self._candidate: State | None = None
        self._candidate_since: datetime | None = None
        self._candidate_samples = 0
        self._last_seen: datetime | None = None
        self._last_evidence_at: datetime | None = None
        self._last_occupancy: datetime | None = None

    # ---------------------------------------------------------------- state
    def to_dict(self) -> dict:
        pending_seconds = 0.0
        if self._candidate_since is not None and self._last_seen is not None:
            pending_seconds = (self._last_seen - self._candidate_since).total_seconds()
        return {
            "current": self.current.value,
            "pending": self._candidate.value if self._candidate else None,
            "pending_seconds": round(pending_seconds, 3),
            "pending_samples": self._candidate_samples,
            "transitions": self.transitions,
            "rejected_transitions": self.rejected_transitions,
            "dropouts": self.dropouts,
            "policy": self.policy.to_dict(),
        }

    # ------------------------------------------------------------- feeding
    def feed_dropout(self, at: datetime) -> State:
        """No usable evidence for this instant.

        Inside the grace window the current state is held and any pending
        candidate is preserved, so a few failed captures do not chop one
        procedure into several. Past the window the machine says UNKNOWN
        rather than continuing to assert a state nobody can see.
        """
        self.dropouts += 1
        reference = self._last_evidence_at or at
        gap = (at - reference).total_seconds()
        if gap > self.policy.dropout_grace_seconds and self.current is not State.UNKNOWN:
            self._commit(State.UNKNOWN, at)
        return self.current

    def feed(self, state: State, at: datetime) -> State:
        """One classified sample. Returns the state the machine is willing to
        stand behind, which is not necessarily the one just proposed."""
        if self._last_evidence_at is not None:
            gap = (at - self._last_evidence_at).total_seconds()
            if gap > self.policy.dropout_grace_seconds:
                # Evidence resumed after a long silence: the pending candidate
                # describes a different era and must not be carried over.
                self._reset_candidate()
        self._last_evidence_at = at
        self._last_seen = at
        if state in _OCCUPANCY_EVIDENCE:
            self._last_occupancy = at

        if state == self.current:
            self._reset_candidate()
            return self.current

        if state != self._candidate:
            self._candidate = state
            self._candidate_since = at
            self._candidate_samples = 1
            return self.current

        self._candidate_samples += 1
        held = (at - self._candidate_since).total_seconds() if self._candidate_since else 0.0
        if self._candidate_samples < self.policy.min_samples:
            return self.current
        if held < self.policy.dwell_for(state):
            return self.current

        target = self._admissible(state, at)
        if target is None:
            self.rejected_transitions += 1
            self._reset_candidate()
            return self.current
        self._commit(target, at)
        return self.current

    # ------------------------------------------------------------ internals
    def _admissible(self, target: State, at: datetime) -> State | None:
        """The state the machine may actually move to, or None.

        An illegal jump is not silently allowed and not silently swallowed
        either: the machine takes one legal step towards the target, so a room
        that really did fill up still gets there — one dwell window later.
        """
        if target is State.TURNOVER and not self._turnover_is_supported(at):
            return None
        if target in ALLOWED_TRANSITIONS[self.current]:
            return target
        return self._bridge(self.current, target, at)

    def _turnover_is_supported(self, at: datetime) -> bool:
        """Turnover means "a room that was just used is being reset".

        Without recent occupancy there is nothing to turn over, and a noisy
        chair reading in an empty room would otherwise become
        billable-looking activity out of thin air.
        """
        if self.current in _OCCUPANCY_EVIDENCE:
            return True
        if self._last_occupancy is None:
            return False
        return (at - self._last_occupancy).total_seconds() <= self.policy.turnover_lookback_seconds

    def _bridge(self, source: State, target: State, at: datetime) -> State | None:
        """One legal step along the shortest path from ``source`` to ``target``."""
        seen = {source}
        frontier: deque[tuple[State, State]] = deque(
            (step, step) for step in sorted(ALLOWED_TRANSITIONS[source], key=lambda s: s.value)
        )
        while frontier:
            node, first = frontier.popleft()
            if node in seen:
                continue
            seen.add(node)
            if node is target:
                if first is State.TURNOVER and not self._turnover_is_supported(at):
                    continue
                return first
            for step in sorted(ALLOWED_TRANSITIONS[node], key=lambda s: s.value):
                if step not in seen:
                    frontier.append((step, first))
        return None

    def _commit(self, state: State, at: datetime) -> None:
        self.current = state
        self.transitions += 1
        self.committed_states.add(state)
        self.history.append(state)
        if len(self.history) > self._history_limit:
            del self.history[0]
        if state in _OCCUPANCY_EVIDENCE:
            self._last_occupancy = at
        self._reset_candidate()

    def _reset_candidate(self) -> None:
        self._candidate = None
        self._candidate_since = None
        self._candidate_samples = 0
