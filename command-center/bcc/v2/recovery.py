from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RecoveryAction = Literal["retry", "restart_component", "fallback", "requeue", "escalate"]

@dataclass(slots=True)
class RecoveryPolicy:
    retry_limit: int = 2
    restart_limit: int = 1
    fallback_allowed: bool = True

    def choose(self, *, retries: int, restarts: int, idempotent: bool,
               fallback_available: bool) -> RecoveryAction:
        if idempotent and retries < self.retry_limit:
            return "retry"
        if restarts < self.restart_limit:
            return "restart_component"
        if self.fallback_allowed and fallback_available:
            return "fallback"
        if idempotent:
            return "requeue"
        return "escalate"
