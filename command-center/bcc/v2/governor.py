from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Literal

Intervention = Literal["none", "warn", "replan", "switch_model", "stop", "ask_human"]

@dataclass(slots=True)
class GovernorThresholds:
    repeated_error_limit: int = 3
    no_progress_steps: int = 6
    max_retries: int = 5
    cloud_budget_usd: float | None = None

@dataclass(slots=True)
class GovernorState:
    thresholds: GovernorThresholds = field(default_factory=GovernorThresholds)
    recent_errors: deque[str] = field(default_factory=lambda: deque(maxlen=12))
    progress_marks: deque[float] = field(default_factory=lambda: deque(maxlen=12))
    retry_count: int = 0
    cloud_spend: float = 0.0

    def record_error(self, signature: str) -> Intervention:
        self.recent_errors.append(signature)
        if Counter(self.recent_errors)[signature] >= self.thresholds.repeated_error_limit:
            return "replan"
        return "none"

    def record_progress(self, value: float) -> Intervention:
        self.progress_marks.append(value)
        if len(self.progress_marks) >= self.thresholds.no_progress_steps:
            tail = list(self.progress_marks)[-self.thresholds.no_progress_steps:]
            if max(tail) - min(tail) <= 1e-9:
                return "replan"
        return "none"

    def record_retry(self) -> Intervention:
        self.retry_count += 1
        if self.retry_count > self.thresholds.max_retries:
            return "ask_human"
        return "none"

    def add_cloud_spend(self, usd: float) -> Intervention:
        self.cloud_spend += max(0.0, usd)
        limit = self.thresholds.cloud_budget_usd
        if limit is not None and self.cloud_spend >= limit:
            return "stop"
        return "none"
