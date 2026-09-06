from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

class Lifecycle(str, Enum):
    DRAFT="DRAFT"; ACTIVE="ACTIVE"; PAUSED="PAUSED"; EXPIRED="EXPIRED"; REVOKED="REVOKED"

class Condition(str, Enum):
    SATISFIED="SATISFIED"; DEVIATED="DEVIATED"; UNKNOWN="UNKNOWN"

@dataclass(frozen=True)
class Observation:
    observation_id: str
    owner_id: str
    scope_id: str
    objective_digest: str
    source_ref: str
    source_revision: str
    observed_at: float
    values: Mapping[str, Any]
    provenance: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class ObjectiveRuntimeState:
    objective_id: str
    owner_id: str
    scope_id: str
    spec_digest: str
    revision: int
    lifecycle: Lifecycle
    condition: Condition
    observations_used: int = 0
    missions_used: int = 0
    wall_seconds_used: float = 0.0
    cost_usd_used: float = 0.0
    last_observation_at: float | None = None
    last_proposal_at: float | None = None
    last_verified_evidence_ref: str | None = None
    stopped: bool = False
    version: int = 0

@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    owner_id: str
    scope_id: str
    objective_id: str
    objective_digest: str
    objective_revision: int
    observation_ids: tuple[str, ...]
    observation_digests: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    expected_effects: tuple[Mapping[str, Any], ...]
    created_at: float
    valid_until: float
    explanation: str = ""

@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str
    reservation_id: str | None = None
    mission_intent_id: str | None = None

@dataclass(frozen=True)
class ReconcileResult:
    condition: Condition
    evidence_refs: tuple[str, ...]
    reason: str
