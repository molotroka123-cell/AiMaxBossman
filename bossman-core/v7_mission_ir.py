"""V7 Adaptive Reality OS - Mission IR (Intermediate Representation) Specification.

Authoritative execution contract for Bossman V7.
Owner Intent -> Reality Compiler -> Mission IR -> World State Graph -> Strategy Engine
"""
from __future__ import annotations

import enum
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


class ObjectivePriority(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    BACKGROUND = "BACKGROUND"


class ConstraintType(str, enum.Enum):
    MAX_LATENCY_MS = "MAX_LATENCY_MS"
    MAX_COST_USD = "MAX_COST_USD"
    MAX_MEMORY_MB = "MAX_MEMORY_MB"
    SANDBOX_ONLY = "SANDBOX_ONLY"
    REQUIRE_USER_CONFIRMATION = "REQUIRE_USER_CONFIRMATION"
    LOCAL_FIRST = "LOCAL_FIRST"


@dataclass
class Objective:
    id: str
    target_state: str
    priority: ObjectivePriority = ObjectivePriority.NORMAL
    required_evidence: List[str] = field(default_factory=list)
    success_criteria: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Constraint:
    type: ConstraintType
    value: Any
    strict: bool = True


@dataclass
class ResourceBudget:
    max_duration_seconds: float = 300.0
    max_cost_usd: float = 0.50
    max_memory_mb: float = 4096.0
    max_retry_budget: int = 3
    disallow_cloud_fallback: bool = False


@dataclass
class MissionIR:
    """The central execution contract for Bossman V7."""
    id: str
    owner_intent: str
    objectives: List[Objective]
    constraints: List[Constraint]
    budget: ResourceBudget
    created_at: float = field(default_factory=time.time)
    provenance_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.provenance_hash:
            payload = {
                "id": self.id,
                "intent": self.owner_intent,
                "created_at": self.created_at,
            }
            self.provenance_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MissionIR:
        data = data.copy()
        data["objectives"] = [Objective(**o) for o in data.get("objectives", [])]
        data["constraints"] = [
            Constraint(type=ConstraintType(c["type"]), value=c["value"], strict=c.get("strict", True))
            for c in data.get("constraints", [])
        ]
        if "budget" in data and isinstance(data["budget"], dict):
            data["budget"] = ResourceBudget(**data["budget"])
        return cls(**data)
