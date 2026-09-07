"""V7 typed Mission IR — compilation target for owner intent.

This module formalizes intent; it never grants authority. Existing V4-V6
permission, budget, privacy and effect gates remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4
import time


class PrivacyClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"


@dataclass(frozen=True)
class Budget:
    money_usd: float | None = None
    tokens: int | None = None
    time_seconds: float | None = None
    memory_mb: int | None = None

    def __post_init__(self) -> None:
        for name, value in (("money_usd", self.money_usd), ("tokens", self.tokens),
                            ("time_seconds", self.time_seconds), ("memory_mb", self.memory_mb)):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class MissionIR:
    owner_intent: str
    objective: str
    desired_state: Sequence[str]
    effect_obligations: Sequence[str] = ()
    proof_obligations: Sequence[str] = ()
    permissions: Sequence[str] = ()
    privacy_class: PrivacyClass = PrivacyClass.INTERNAL
    budget: Budget = field(default_factory=Budget)
    current_state_assumptions: Mapping[str, Any] = field(default_factory=dict)
    deadline_epoch_s: float | None = None
    priority: int = 50
    rollback_contract: str | None = None
    uncertainty_policy: str = "fail_closed"
    escalation_conditions: Sequence[str] = ()
    mission_id: str = field(default_factory=lambda: str(uuid4()))
    version: int = 1
    created_at_epoch_s: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.owner_intent.strip() or not self.objective.strip():
            raise ValueError("owner_intent and objective are required")
        if not self.desired_state:
            raise ValueError("at least one desired-state predicate is required")
        if not 0 <= self.priority <= 100:
            raise ValueError("priority must be in [0, 100]")
        if self.uncertainty_policy not in {"fail_closed", "escalate", "observe_more"}:
            raise ValueError("unsupported uncertainty_policy")

    @property
    def is_effectful(self) -> bool:
        return bool(self.effect_obligations)

    def validate_execution_readiness(self) -> tuple[bool, tuple[str, ...]]:
        """Structural readiness only; this does not authorize execution."""
        errors: list[str] = []
        if self.is_effectful and not self.proof_obligations:
            errors.append("effectful missions require explicit proof obligations")
        if self.is_effectful and not self.rollback_contract:
            errors.append("effectful missions require a rollback contract")
        return not errors, tuple(errors)
