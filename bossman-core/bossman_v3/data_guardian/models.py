from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

@dataclass(frozen=True)
class ContextItem:
    item_id: str
    category: str
    content: str | None = None
    raw_ref: str | None = None
    token_count: int = 0
    priority: int = 6
    importance: float = 0.0
    uncertainty: float = 0.0
    irrecoverability: float = 0.0
    probability_important: float = 0.0
    impact: float = 0.0
    savings_utility: float = 0.0
    source: str = "unknown"
    version: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    protected: bool = False
    conflict_group: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class GuardianConfig:
    token_budget: int = 8000
    mandatory_priorities: tuple[int, ...] = (0, 1, 2)
    keep_risk_threshold: float = 0.35
    deep_context_threshold: float = 0.45
    max_verified_success_degradation: float = 0.01
    low_memory_budget: int = 3000
    protected_categories: tuple[str, ...] = (
        "security", "policy", "objective", "constraint", "fresh_observation",
        "active_error", "key_decision", "p0", "p1",
    )

@dataclass(frozen=True)
class GuardianReport:
    selected: tuple[ContextItem, ...]
    omitted: tuple[ContextItem, ...]
    raw_fallback_refs: tuple[str, ...]
    deep_escalated: bool
    reasons: tuple[str, ...]
    selected_tokens: int
    original_tokens: int

@dataclass(frozen=True)
class RetentionMetrics:
    intelligence_retention: float
    context_gain: float
    absolute_success_degradation: float
    production_allowed: bool
