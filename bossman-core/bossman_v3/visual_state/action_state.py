"""Opt-in pre-dispatch semantic checks. No executor or authorization bypass.

Adapters must collect fragments themselves inside the existing execution guard,
check immediately before dispatch while owning input/window state, and reobserve
on any state change. A successful check is neither policy approval nor evidence
that an effect happened. Untrusted model-provided identities are not sufficient.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable, Mapping

from bossman_v3.contracts import TypedAction
from .fusion import AmbiguousVisualStateError, VisualStateEngine
from .models import StateFragment, StateIdentity, VisualSnapshot, _canonical_json


def _action_digest(action: TypedAction) -> str:
    serialized = _canonical_json({
        "action_type": action.action_type,
        "args": dict(action.args),
        "scopes": action.scopes,
        "side_effect": action.side_effect.value,
        "idempotency_key": action.idempotency_key,
        "source": action.source,
    })
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionStateBinding:
    identity: StateIdentity
    action_digest: str
    required_state_json: str


class SemanticActionStateGuard:
    """Require the same action, identity and structured semantic preconditions.

    Callers explicitly opt in by constructing this guard; legacy UCA callers
    remain unchanged. Required keys should identify the element and its meaning,
    enabled/focus/modal state as relevant. Coordinates alone are insufficient.
    Every check re-fuses all fragments using the strict contract.
    """

    def __init__(self, max_age_seconds: float = 5.0, *, min_structured_confidence: float = 0.8):
        self.engine = VisualStateEngine(
            max_age_seconds, require_identity=True,
            min_structured_confidence=min_structured_confidence,
        )

    def bind(self, action: TypedAction, fragments: Iterable[StateFragment], *,
             required_state: Mapping[str, Any], now: datetime | None = None) -> ActionStateBinding:
        action_digest = _action_digest(action)
        required = dict(required_state)
        if not required:
            raise ValueError("at least one semantic precondition is required")
        required_json = _canonical_json(required)
        snapshot = self.engine.fuse(fragments, now=now)
        self._check_semantics(snapshot, json.loads(required_json))
        if _action_digest(action) != action_digest:
            raise AmbiguousVisualStateError("action changed during state binding; rebind required")
        return ActionStateBinding(snapshot.identity, action_digest, required_json)

    def check(self, action: TypedAction, binding: ActionStateBinding,
              fragments: Iterable[StateFragment], *, now: datetime | None = None) -> None:
        if _action_digest(action) != binding.action_digest:
            raise AmbiguousVisualStateError("action changed after state binding; rebind required")
        snapshot = self.engine.fuse(fragments, now=now)
        if snapshot.identity != binding.identity:
            raise AmbiguousVisualStateError("application/window/document state changed; reobserve and rebind")
        required = json.loads(binding.required_state_json)
        if not isinstance(required, dict) or not required:
            raise ValueError("binding has no semantic preconditions")
        self._check_semantics(snapshot, required)
        # Observation iterators/adapters can mutate the caller-owned TypedAction
        # args while fusion runs. Check again after consuming and inspecting it.
        if _action_digest(action) != binding.action_digest:
            raise AmbiguousVisualStateError("action changed during state check; rebind required")

    @staticmethod
    def _check_semantics(snapshot: VisualSnapshot, required: Mapping[str, Any]) -> None:
        for key, value in required.items():
            if key not in snapshot.authoritative_fields:
                raise AmbiguousVisualStateError(f"no structured observation for semantic field: {key}")
            if _canonical_json(snapshot.structured[key]) != _canonical_json(value):
                raise AmbiguousVisualStateError(f"semantic precondition changed: {key}")
