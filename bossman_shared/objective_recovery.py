"""Restart recovery, the crash matrix and long-horizon resume for objectives.

A process that dies mid-mission leaves reservations in flight. This module
resolves each one EXPLICITLY from an observer-backed answer, or refuses to
resolve it at all. Ambiguity is a first-class outcome (`PARKED`), not a default
that quietly picks the convenient branch.

Deliberate non-goals. Nothing here dispatches, retries, approves, compensates or
rolls anything back. `recover` settles bookkeeping and lowers health; re-running
work happens later under a fresh admission with fresh evidence.
`prepare_rollback` returns the rollback *order* as inspectable data and executes
none of it.

Invariants:

* Crash ambiguity NEVER authorizes an irreversible replay. UNKNOWN, or any
  IRREVERSIBLE effect that was not observed to have landed, parks for the owner.
* An effect class that was not recorded is treated as IRREVERSIBLE: the safe
  default for a missing fact is the one that cannot be undone.
* Settling a reservation is bookkeeping, not proof. Every recovery leaves the
  condition UNKNOWN pending a fresh re-observation
  (`objective_reconcile.reconcile_after_mission`).
* Resume is from LAST_VERIFIED_STATE — the last verified evidence ref, last
  observation time and cumulative usage — never from a remembered plan.
* Retries are bounded. Exhausting the budget is a terminal blocked outcome that
  asks the owner; there is no unbounded retry anywhere in this module.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any

from .objective_store import ObjectiveStore, ObjectiveStoreError

APPLIED = "APPLIED"
NOT_APPLIED = "NOT_APPLIED"
UNKNOWN = "UNKNOWN"
EFFECT_ANSWERS = frozenset({APPLIED, NOT_APPLIED, UNKNOWN})

IDEMPOTENT = "IDEMPOTENT"
REVERSIBLE = "REVERSIBLE"
IRREVERSIBLE = "IRREVERSIBLE"
EFFECT_CLASSES = frozenset({IDEMPOTENT, REVERSIBLE, IRREVERSIBLE})
RETRYABLE_CLASSES = frozenset({IDEMPOTENT, REVERSIBLE})

COMMITTED = "COMMITTED"
RELEASED = "RELEASED"
PARKED = "PARKED"

REASON_APPLIED = "effect_observed_applied"
REASON_RETRYABLE = "effect_absent_and_retryable"
REASON_AMBIGUOUS = "effect_unknown_after_crash"
REASON_IRREVERSIBLE = "irreversible_effect_not_confirmed"
REASON_BAD_ANSWER = "observer_answer_uninterpretable"
REASON_SETTLE_CONFLICT = "reservation_already_settled"
REASON_BUDGET_EXHAUSTED = "retry_budget_exhausted"
REASON_WITHIN_BUDGET = "attempt_within_budget"

DEFAULT_ATTEMPT_BUDGET = 3
# The handoff's rollback ordering, as data. Observers stop first so nothing new
# arrives; effects are fenced before missions drain so a straggler cannot land
# after the snapshot is taken.
ROLLBACK_ORDER = (
    ("pause_observers", "no new observations may enter a runtime being withdrawn"),
    ("stop_admissions", "no proposal may become work after observers are paused"),
    ("fence_effects", "in-flight executors must be fenced before missions drain"),
    ("drain_park_missions", "running missions drain; ambiguous ones park for the owner"),
    ("snapshot_objective_state", "objective bookkeeping is captured read-only, last"),
)


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    """How one in-flight reservation was resolved, and why."""

    reservation_id: str
    proposal_id: str
    effect_class: str
    answer: str
    disposition: str
    reason: str
    requires_owner: bool


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Result of one restart pass. Blocked means: stop, ask the owner."""

    objective_id: str
    outcomes: tuple[ReservationOutcome, ...]
    condition: str
    blocked: bool
    version: int

    @property
    def parked(self) -> tuple[ReservationOutcome, ...]:
        return tuple(o for o in self.outcomes if o.disposition == PARKED)


@dataclass(frozen=True, slots=True)
class ResumePoint:
    """LAST_VERIFIED_STATE: what is known to be true, not what was planned."""

    objective_id: str
    lifecycle: str
    condition: str
    last_verified_evidence_ref: str | None
    last_observation_at: float | None
    last_proposal_at: float | None
    observations_used: int
    missions_used: int
    wall_seconds_used: float
    cost_usd_used: float
    open_reservations: tuple[str, ...]
    version: int

    @property
    def has_verified_state(self) -> bool:
        """True only if something was actually verified; UNKNOWN is not proof."""
        return bool(self.last_verified_evidence_ref) and self.condition != "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """A bounded attempt decision; `blocked` is terminal and needs an owner."""

    allowed: bool
    attempt: int
    budget: int
    reason: str
    blocked: bool


@dataclass(frozen=True, slots=True)
class RollbackStep:
    order: int
    action: str
    why: str


@dataclass(frozen=True, slots=True)
class RollbackPlan:
    """An inspectable plan. Constructing it executes and destroys nothing."""

    objective_id: str
    prepared_at: float
    steps: tuple[RollbackStep, ...]
    open_reservations: tuple[str, ...]
    snapshot_json: str
    executed: bool = False

    def snapshot(self) -> dict[str, Any]:
        return json.loads(self.snapshot_json)


def bounded_retry(attempts_made: int, *, budget: int = DEFAULT_ATTEMPT_BUDGET) -> RetryDecision:
    """Refuse attempt N+1 past the budget instead of looping.

    A recovery loop that retries "until it works" is how one crash becomes a
    thousand effects; the terminal state is an owner question, not a sleep.
    """
    if type(attempts_made) is not int or attempts_made < 0:
        raise ValueError("attempts_made must be a nonnegative integer")
    if type(budget) is not int or budget < 1:
        raise ValueError("attempt budget must be a positive integer")
    if attempts_made >= budget:
        return RetryDecision(False, attempts_made, budget, REASON_BUDGET_EXHAUSTED, True)
    return RetryDecision(True, attempts_made + 1, budget, REASON_WITHIN_BUDGET, False)


def _effect_class(payload: Mapping[str, Any]) -> str:
    value = payload.get("effect_class") if isinstance(payload, Mapping) else None
    return value if value in EFFECT_CLASSES else IRREVERSIBLE


def _decide(effect_class: str, answer: str) -> tuple[str, str, bool]:
    if answer == APPLIED:
        return COMMITTED, REASON_APPLIED, False
    if answer == NOT_APPLIED:
        if effect_class in RETRYABLE_CLASSES:
            # Released, not re-dispatched: retry is a fresh admission's decision.
            return RELEASED, REASON_RETRYABLE, False
        return PARKED, REASON_IRREVERSIBLE, True
    if answer == UNKNOWN:
        return PARKED, REASON_AMBIGUOUS, True
    return PARKED, REASON_BAD_ANSWER, True


def recover(store: ObjectiveStore, objective_id: str, *, now: float,
            is_effect_applied: Callable[[Mapping[str, Any]], str],
            expected_version: int | None = None) -> RecoveryReport:
    """Resolve every in-flight reservation explicitly after a restart.

    `is_effect_applied` must be observer-backed and three-valued; a caller that
    can only guess must answer UNKNOWN, which parks. Nothing is dispatched here
    under any branch.
    """
    if not callable(is_effect_applied):
        raise ValueError("an observer-backed three-valued effect probe is required")
    state = store.get(objective_id)
    if expected_version is not None and state.version != expected_version:
        raise ObjectiveStoreError("stale objective state; re-read before recovering")
    version = state.version
    outcomes: list[ReservationOutcome] = []
    for reservation in store.open_reservations(objective_id):
        payload = reservation.get("payload") or {}
        effect_class = _effect_class(payload)
        try:
            answer = is_effect_applied(reservation)
        except Exception:  # a probe that fails answers UNKNOWN, never APPLIED
            answer = UNKNOWN
        if answer not in EFFECT_ANSWERS:
            answer = UNKNOWN if answer is None else str(answer)
        disposition, reason, requires_owner = _decide(effect_class, answer)
        if disposition in (COMMITTED, RELEASED):
            try:
                # Расход списывает сама settle_reservation, в одной транзакции с
                # закрытием брони. Отдельное начисление здесь считало бы бюджет
                # дважды — и читало числа не оттуда: они лежат внутри `estimate`,
                # а плоское чтение всегда давало ноль.
                settled = store.settle_reservation(reservation["reservation_id"], disposition)
                version = settled.version
            except ObjectiveStoreError:
                disposition, reason, requires_owner = PARKED, REASON_SETTLE_CONFLICT, True
        outcomes.append(ReservationOutcome(
            reservation["reservation_id"], reservation["proposal_id"], effect_class,
            answer if answer in EFFECT_ANSWERS else UNKNOWN, disposition, reason,
            requires_owner))
    condition = state.condition
    if outcomes and condition != "UNKNOWN":
        # A settled reservation is bookkeeping, not world state: whatever the
        # condition said before the crash is now unproven. The previously
        # verified reference is carried forward so resume still has a fact.
        written = store.set_condition(objective_id, "UNKNOWN",
                                      evidence_ref=state.last_verified_evidence_ref,
                                      expected_version=version)
        condition, version = "UNKNOWN", written.version
    return RecoveryReport(objective_id, tuple(outcomes), condition,
                          any(o.requires_owner for o in outcomes), version)


def resume_point(store: ObjectiveStore, objective_id: str) -> ResumePoint:
    """Report LAST_VERIFIED_STATE for a long-horizon resume; writes nothing."""
    state = store.get(objective_id)
    open_ids = tuple(r["reservation_id"] for r in store.open_reservations(objective_id))
    return ResumePoint(
        objective_id=state.objective_id, lifecycle=state.lifecycle, condition=state.condition,
        last_verified_evidence_ref=state.last_verified_evidence_ref,
        last_observation_at=state.last_observation_at, last_proposal_at=state.last_proposal_at,
        observations_used=state.observations_used, missions_used=state.missions_used,
        wall_seconds_used=state.wall_seconds_used, cost_usd_used=state.cost_usd_used,
        open_reservations=open_ids, version=state.version)


def prepare_rollback(store: ObjectiveStore, objective_id: str, *, now: float) -> RollbackPlan:
    """Build the handoff's rollback ordering as data, plus a read-only snapshot.

    Rehearsal is the point: the plan is inspectable and diffable before anyone
    is allowed to run it, and preparing it never pauses, fences or drains.
    """
    point = resume_point(store, objective_id)
    steps = tuple(RollbackStep(i + 1, action, why)
                  for i, (action, why) in enumerate(ROLLBACK_ORDER))
    snapshot = {
        "objective_id": point.objective_id, "lifecycle": point.lifecycle,
        "condition": point.condition,
        "last_verified_evidence_ref": point.last_verified_evidence_ref,
        "last_observation_at": point.last_observation_at,
        "observations_used": point.observations_used, "missions_used": point.missions_used,
        "wall_seconds_used": point.wall_seconds_used, "cost_usd_used": point.cost_usd_used,
        "open_reservations": list(point.open_reservations), "version": point.version,
        "prepared_at": now,
    }
    return RollbackPlan(objective_id, now, steps, point.open_reservations,
                        json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
