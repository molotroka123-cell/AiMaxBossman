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


LaneName = Literal["owner_generation", "interactive", "background"]


@dataclass(slots=True)
class LaneTask:
    task_id: str | int
    lane: LaneName
    priority: int = 5


def schedule_lanes(tasks: list[LaneTask], *, active_owner_generations: int = 0) -> dict:
    """Lane scheduler: owner generations always preempt; background tasks pause when GPU generation is active."""
    lane_order = {"owner_generation": 0, "interactive": 1, "background": 2}
    runnable: list[str | int] = []
    deferred_background: list[str | int] = []
    for t in sorted(tasks, key=lambda x: (lane_order.get(x.lane, 9), -int(x.priority), str(x.task_id))):
        if t.lane == "background" and active_owner_generations > 0:
            deferred_background.append(t.task_id)
        else:
            runnable.append(t.task_id)
    return {
        "active_owner_generations": active_owner_generations,
        "runnable": runnable,
        "deferred_background": deferred_background,
        "owner_preempts_background": True,
    }


def classify_and_recover(error_text: str, *, retries: int = 0, fallback_available: bool = True,
                         base_backoff_s: float = 30.0) -> dict:
    """Failure taxonomy with fast recovery (< 0.5 of uncalibrated backoff) for transient and fallback-eligible errors."""
    low = (error_text or "").lower()
    if any(k in low for k in ("401", "403", "unauthorized", "invalid api key", "spend meter")):
        return {"category": "auth_or_budget", "action": "escalate", "delay_s": 0.0, "naive_delay_s": base_backoff_s}
    if any(k in low for k in ("429", "rate limit", "quota", "throttled")) and fallback_available:
        # Immediate failover to healthy free/local model instead of waiting 30s
        return {"category": "rate_limit", "action": "fallback", "delay_s": 0.5, "naive_delay_s": base_backoff_s}
    if any(k in low for k in ("timeout", "timed out", "502", "503", "connection")):
        action = "fallback" if (retries >= 1 and fallback_available) else "retry"
        delay = 1.0 if action == "fallback" else min(5.0, base_backoff_s * 0.2)
        return {"category": "transient_network", "action": action, "delay_s": delay, "naive_delay_s": base_backoff_s}
    return {"category": "permanent_or_unknown", "action": "escalate", "delay_s": 0.0, "naive_delay_s": base_backoff_s}

