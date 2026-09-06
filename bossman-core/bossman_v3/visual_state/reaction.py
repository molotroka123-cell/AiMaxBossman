"""Disabled synchronous reaction foundation; no OS input or execution path.

One serialized owner feeds trusted capture-order sequence numbers and fragments.
The latest coherent observation replaces previous observations; any semantic or
identity change invalidates the single pending action. An interrupt latches until
explicit resume, which requires a new capture. No worker/thread is created.

A ready result only requests the canonical policy/approval/execution gate. It is
not an authorization, receipt or promise of human-level latency. The production
adapter must own input and immutable action state, recollect/check immediately
before the actual effect, and preserve all mission obligations outside this
single pending-action primitive. There is no durable recovery or OS integration.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import time
from typing import Any, Callable, Iterable, Mapping

from bossman_v3.contracts import TypedAction
from .action_state import ActionStateBinding, SemanticActionStateGuard
from .models import StateFragment, _canonical_json, _freeze_json


class ReactionStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReactionDecision:
    ready_for_canonical_gate: bool
    reason: str
    action: TypedAction | None = None

    @property
    def dispatch_authorized(self) -> bool:
        return False


@dataclass(frozen=True)
class _Pending:
    action: TypedAction
    binding: ActionStateBinding
    deadline: float


class ReactionCoordinator:
    """One pending action and one observation batch; opt in with enabled=True.

    capture_sequence and captured_monotonic must be supplied by the trusted
    adapter at capture, not arrival; captures after resume must be newer than
    its monotonic barrier. Out-of-order deliveries cannot replace current state. Methods need
    one serialized caller; this object is not a cross-thread synchronization lock.
    Capture count is capped; timestamps, identity and JSON payload validity are
    delegated to the existing strict visual contract.
    """

    def __init__(self, *, enabled: bool = False, max_age_seconds: float = 5.0,
                 clock: Callable[[], float] = time.monotonic):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        self._enabled = enabled
        self._guard = SemanticActionStateGuard(max_age_seconds)
        self._clock = clock
        self._last_tick: float | None = None
        self._sequence = -1
        self._control_generation = 0
        self._capture_floor = -1.0
        self._last_capture_tick = -1.0
        self._interrupted = False
        self._latest: tuple[StateFragment, ...] = ()
        self._fingerprint: str | None = None
        self._observed_at: datetime | None = None
        self._received_tick: float | None = None
        self._pending: _Pending | None = None

    def _clear(self) -> None:
        self._latest = ()
        self._fingerprint = None
        self._received_tick = None
        self._pending = None

    def _tick(self) -> float:
        value = self._clock()
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or self._last_tick is not None and value < self._last_tick):
            self._clear()
            raise ReactionStateError("invalid or regressed monotonic clock")
        self._last_tick = value
        return value

    def interrupt(self) -> None:
        self._capture_floor = self._tick()
        self._control_generation += 1
        self._interrupted = True
        self._clear()

    def resume(self) -> None:
        self._capture_floor = self._tick()
        self._control_generation += 1
        self._clear()
        self._interrupted = False

    def observe(self, fragments: Iterable[StateFragment], *, capture_sequence: int,
                captured_monotonic: float,
                now: datetime | None = None) -> bool:
        tick = self._tick()
        if not self._enabled:
            return False
        if type(capture_sequence) is not int or capture_sequence < 0:
            self._clear()
            raise ValueError("capture_sequence must be a nonnegative integer")
        if capture_sequence <= self._sequence:
            return False
        self._sequence = capture_sequence
        generation = self._control_generation
        if self._interrupted:
            return False
        try:
            if (type(captured_monotonic) not in (int, float) or not math.isfinite(captured_monotonic)
                    or captured_monotonic < 0 or captured_monotonic > tick
                    or captured_monotonic <= self._capture_floor
                    or captured_monotonic < self._last_capture_tick
                    or tick - captured_monotonic > self._guard.engine.max_age_seconds):
                raise ReactionStateError("stale, out-of-order or pre-resume capture time")
            detached = []
            for fragment in fragments:
                if len(detached) >= 128:
                    raise ReactionStateError("observation batch exceeds 128 fragments")
                detached.append(replace(fragment, payload=_freeze_json(fragment.payload)))
            batch = tuple(detached)
            snapshot = self._guard.engine.fuse(batch, now=now)
            if self._observed_at is not None and snapshot.observed_at < self._observed_at:
                raise ReactionStateError("observation time moved backwards")
            # Callback/iterator reentry may have delivered a newer capture or
            # interrupted the owner while materializing this batch.
            if (self._interrupted or capture_sequence != self._sequence
                    or generation != self._control_generation):
                return False
            fingerprint = _canonical_json({
                "identity": vars(snapshot.identity),
                "structured": {k: snapshot.structured[k] for k in snapshot.authoritative_fields},
            })
            if self._tick() - captured_monotonic > self._guard.engine.max_age_seconds:
                raise ReactionStateError("observation processing exceeded freshness deadline")
            if fingerprint != self._fingerprint:
                self._pending = None
            self._latest = batch
            self._fingerprint = fingerprint
            self._observed_at = snapshot.observed_at
            self._received_tick = captured_monotonic
            self._last_capture_tick = captured_monotonic
            return True
        except Exception:
            self._clear()
            raise

    def queue_action(self, action: TypedAction, *, required_state: Mapping[str, Any],
                     timeout_seconds: float, now: datetime | None = None) -> None:
        tick = self._tick()
        if not self._enabled or self._interrupted or not self._latest:
            raise ReactionStateError("enabled, resumed coordinator and fresh capture required")
        if self._pending is not None:
            raise ReactionStateError("one action is already pending")
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0 or not math.isfinite(tick + timeout_seconds)):
            raise ValueError("timeout_seconds must be finite and positive")
        if tick - self._received_tick > self._guard.engine.max_age_seconds:
            self._clear()
            raise ReactionStateError("observation expired on monotonic clock")
        generation, sequence = self._control_generation, self._sequence
        frozen_action = replace(action, args=_freeze_json(action.args), scopes=tuple(action.scopes))
        binding = self._guard.bind(frozen_action, self._latest, required_state=required_state, now=now)
        if (generation != self._control_generation or sequence != self._sequence
                or self._interrupted or not self._latest):
            raise ReactionStateError("state or owner control changed while queueing")
        if self._tick() >= tick + timeout_seconds:
            raise ReactionStateError("queueing exceeded action deadline")
        self._pending = _Pending(frozen_action, binding, tick + timeout_seconds)

    def poll(self, *, now: datetime | None = None) -> ReactionDecision:
        tick = self._tick()
        if not self._enabled:
            return ReactionDecision(False, "disabled")
        if self._interrupted:
            return ReactionDecision(False, "owner_interrupted")
        pending = self._pending
        if pending is None:
            return ReactionDecision(False, "no_pending_action")
        if tick >= pending.deadline:
            self._pending = None
            return ReactionDecision(False, "deadline_expired")
        if self._received_tick is None or tick - self._received_tick > self._guard.engine.max_age_seconds:
            self._clear()
            return ReactionDecision(False, "observation_expired")
        try:
            self._guard.check(pending.action, pending.binding, self._latest,
                              now=now or datetime.now(timezone.utc))
        except (ValueError, TypeError, RuntimeError):
            self._clear()
            return ReactionDecision(False, "state_recheck_failed")
        final_tick = self._tick()
        if self._interrupted or self._pending is not pending:
            return ReactionDecision(False, "state_or_owner_changed")
        if final_tick >= pending.deadline:
            self._pending = None
            return ReactionDecision(False, "deadline_expired")
        if self._received_tick is None or final_tick - self._received_tick > self._guard.engine.max_age_seconds:
            self._clear()
            return ReactionDecision(False, "observation_expired")
        self._pending = None
        return ReactionDecision(True, "canonical_gate_required", pending.action)
