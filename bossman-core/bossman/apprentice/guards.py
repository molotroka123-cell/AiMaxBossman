"""Thin guards: observation freshness, semantic anchor resolution, side-effect
idempotency ledger and approval validation. Reuses loop_guard.state_signature
for hashes and company.model.ApprovalDecision for approvals."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from bossman.company.model import ApprovalDecision
from bossman.computer_operator.loop_guard import state_signature
from bossman.computer_operator.models import Observation

from . import flags
from .models import ObservationRef, SemanticTarget, sha


def observation_hash(obs: Observation) -> str:
    """Content hash: foreground + summary + full UI tree (text included), unlike
    loop_guard.state_signature which deliberately ignores text for loop detection."""
    return sha("obs", obs.foreground, obs.summary, obs.ui_tree, bool(obs.sensitive))


def loop_signature(obs: Observation) -> str:
    return state_signature(obs)


def observation_ref(obs: Observation) -> ObservationRef:
    return ObservationRef.of(obs, observation_hash(obs))


def freshness_error(ref: ObservationRef, latest: Observation | None, *, current_generation: int) -> str:
    """Empty string = the ref is the latest observation and nothing newer exists."""
    if latest is None:
        return "no observation taken"
    if ref.id != latest.id:
        return f"observation {ref.id} is not the latest ({latest.id})"
    if ref.generation != int(latest.generation) or ref.generation != current_generation:
        return f"observation generation {ref.generation} != current {current_generation}"
    if ref.hash != observation_hash(latest):
        return "observation content changed since it was taken"
    return ""


# ------------------------------------------------------------------ anchors
READY_THRESHOLD = 0.75


def _elements(ui_tree: Any) -> list[dict]:
    if isinstance(ui_tree, dict) and isinstance(ui_tree.get("elements"), list):
        return [e for e in ui_tree["elements"] if isinstance(e, dict)]
    return []


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def match_score(target: SemanticTarget, element: dict) -> float:
    """Weighted anchor agreement in [0, 1]. Role+name are primary; text /
    description / extra anchors add redundancy (proposal P3)."""
    checks: list[tuple[float, bool]] = []
    role = _norm(element.get("role") or element.get("control_type"))
    name = _norm(element.get("name") or element.get("label"))
    text = _norm(element.get("text"))
    desc = _norm(element.get("description"))
    hay = " ".join([role, name, text, desc, _norm(element.get("aria_label")),
                    " ".join(_norm(x) for x in (element.get("neighbors") or []))])
    if target.role:
        checks.append((0.35, _norm(target.role) == role))
    if target.name:
        checks.append((0.35, _norm(target.name) == name or _norm(target.name) in hay))
    if target.text:
        checks.append((0.15, _norm(target.text) in (text + " " + name)))
    if target.description:
        checks.append((0.05, _norm(target.description) in hay))
    for a in target.anchors:
        checks.append((0.10, _norm(a) in hay))
    if not checks:
        return 0.0
    total = sum(w for w, _ in checks)
    return round(sum(w for w, ok in checks if ok) / total, 4)


@dataclass(frozen=True, slots=True)
class Resolution:
    element: dict | None
    score: float
    state: str          # READY | DEGRADED | INAPPLICABLE


def resolve_target(target: SemanticTarget, obs: Observation) -> Resolution:
    best, best_score = None, 0.0
    for el in _elements(obs.ui_tree):
        s = match_score(target, el)
        if s > best_score:
            best, best_score = el, s
    if best is None or best_score <= 0:
        return Resolution(None, 0.0, "INAPPLICABLE")
    if flags.enabled(flags.ANCHOR_REDUNDANCY):
        return Resolution(best if best_score >= READY_THRESHOLD else None, best_score,
                          "READY" if best_score >= READY_THRESHOLD else "DEGRADED")
    exact = _norm(target.role) == _norm(best.get("role") or best.get("control_type")) and (
        not target.name or _norm(target.name) == _norm(best.get("name") or best.get("label")))
    return Resolution(best if exact else None, best_score, "READY" if exact else "DEGRADED")


# ------------------------------------------------------------------ side effects
class SideEffectLedger:
    """Process-local idempotency ledger shared by all engines of a process. Keyed
    by side_effect_id; the first claim wins, later claims see the stored result."""

    def __init__(self, store: Any | None = None) -> None:
        self._lock = threading.Lock()
        self.store = store
        self._done: dict[str, dict] = {}
        self._claimed: set[str] = set()

    def claim(self, side_effect_id: str) -> tuple[bool, dict | None]:
        if self.store is not None:
            return self.store.claim_side_effect(side_effect_id)
        with self._lock:
            if side_effect_id in self._done:
                return False, self._done[side_effect_id]
            if side_effect_id in self._claimed:
                return False, None
            self._claimed.add(side_effect_id)
            return True, None

    def complete(self, side_effect_id: str, result: dict) -> None:
        if self.store is not None:
            self.store.complete_side_effect(side_effect_id, result)
            return
        with self._lock:
            self._claimed.discard(side_effect_id)
            self._done[side_effect_id] = dict(result)

    def abandon(self, side_effect_id: str) -> None:
        if self.store is not None:
            self.store.abandon_side_effect(side_effect_id)
            return
        with self._lock:
            self._claimed.discard(side_effect_id)

    def seen(self, side_effect_id: str) -> bool:
        if self.store is not None:
            return self.store.side_effect_seen(side_effect_id)
        with self._lock:
            return side_effect_id in self._done

    def __len__(self) -> int:
        return len(self._done)


def side_effect_id(task_id: str, step_id: str, kind: str, target_label: str, text: str, args: dict, key: str = "",
                   *, session_id: str = "", app: str = "") -> str:
    """Identity of an external effect.

    With an explicit idempotency_key the identity is scoped to session + application +
    action kind + target + key — NOT to task_id/run_id, which differ on every retry, so a
    retried run cannot execute the same keyed effect twice. Without a key the deterministic
    task-scoped step identity is used."""
    if key:
        return sha("side_effect", "key", session_id, app, kind, target_label, key)[:32]
    return sha("side_effect", task_id, step_id, kind, target_label, sha(text), args)[:32]


# ------------------------------------------------------------------ approvals
def step_digest(task_id: str, step_id: str, kind: str, target_label: str, text: str, args: dict) -> str:
    """Canonical identity of the action being approved (mirrors company.model.task_digest)."""
    return sha("approval", task_id, step_id, kind, target_label, sha(text), args)


class ApprovalRegistry:
    """One-time nonce consumption. Validation mirrors company.runtime._valid_approval."""

    def __init__(self, clock: Callable[[], float] = time.time, store: Any | None = None) -> None:
        self.clock = clock
        self.store = store
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    def validate(self, d: Any, *, digest: str, scope: str) -> str:
        if not isinstance(d, ApprovalDecision):
            return f"gate returned {type(d).__name__}, not ApprovalDecision"
        if not d.approved:
            return d.reason or "denied"
        if d.digest != digest:
            return "approval digest does not match this task/action"
        if d.scope != scope:
            return "approval scope is another task"
        if d.expires_at is not None and self.clock() >= d.expires_at:
            return "approval expired"
        if not d.nonce:
            return "approval without nonce (one-time consumption impossible)"
        if self.store is not None and self.store.nonce_consumed(d.nonce):
            return "approval already consumed (replay)"
        with self._lock:
            if d.nonce in self._consumed:
                return "approval already consumed (replay)"
        return ""

    def consume(self, d: ApprovalDecision) -> None:
        if self.store is not None:
            if not self.store.consume_nonce_once(d.nonce):
                raise RuntimeError("approval nonce was already consumed")
            return
        with self._lock:
            self._consumed.add(d.nonce)
