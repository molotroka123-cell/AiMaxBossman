"""Host-bound visual pre-dispatch adapter for the canonical UCA.

Bind a semantic target from the trusted initial capture, then install this check
as ``UniversalComputerAgent(..., pre_dispatch=...)``. The host must own OS input
in its existing execution_guard and capture current state synchronously there.
A successful check is not approval, a receipt, or effect verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from ..contracts import TypedAction
from .action_state import ActionStateBinding, SemanticActionStateGuard
from .fusion import AmbiguousVisualStateError
from .models import StateFragment


@dataclass(frozen=True)
class BoundVisualDispatch:
    binding: ActionStateBinding
    capture: Callable[[TypedAction], Iterable[StateFragment]]
    guard: SemanticActionStateGuard

    def __call__(self, action: TypedAction, context: Mapping[str, Any]) -> None:
        from ..computer_agent.agent import StaleObservationError
        if not callable(context.get("execution_guard")):
            raise StaleObservationError("visual dispatch requires a host-owned execution guard")
        try:
            self.guard.check(action, self.binding, self.capture(action))
        except (AmbiguousVisualStateError, ValueError) as exc:
            raise StaleObservationError(f"visual re-observation required: {exc}") from exc
