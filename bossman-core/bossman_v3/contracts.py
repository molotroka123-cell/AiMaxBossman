from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SideEffectClass(str, Enum):
    READ_ONLY = "READ_ONLY"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    IRREVERSIBLE = "IRREVERSIBLE"


@dataclass(frozen=True)
class TypedAction:
    action_type: str
    args: Mapping[str, Any] = field(default_factory=dict)
    scopes: tuple[str, ...] = ()
    side_effect: SideEffectClass = SideEffectClass.READ_ONLY
    idempotency_key: str | None = None
    source: str = "bossman_v3"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    approval_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class ExecutionReceipt:
    action_type: str
    started_at: datetime
    completed_at: datetime
    effect_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    source: str
    state: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()


class PolicyPort(Protocol):
    def authorize(self, action: TypedAction, context: Mapping[str, Any]) -> PolicyDecision: ...


class ApprovalPort(Protocol):
    def request(self, action: TypedAction, policy: PolicyDecision, context: Mapping[str, Any]) -> ApprovalDecision: ...


class ExecutorPort(Protocol):
    def supports(self, action_type: str) -> bool: ...
    def execute(self, action: TypedAction) -> ExecutionReceipt: ...


class ObservationPort(Protocol):
    def observe_fresh(self, action: TypedAction, receipt: ExecutionReceipt) -> Observation: ...


class VerifierPort(Protocol):
    def verify(
        self,
        action: TypedAction,
        receipt: ExecutionReceipt,
        observation: Observation,
    ) -> VerificationResult: ...


class FailureMemoryPort(Protocol):
    def query(self, signature: str, limit: int = 20) -> Sequence[Mapping[str, Any]]: ...
    def record(self, event: Mapping[str, Any]) -> None: ...


class ArtifactStorePort(Protocol):
    def put(self, content: bytes, *, media_type: str, metadata: Mapping[str, Any] | None = None) -> str: ...
    def get(self, ref: str) -> bytes: ...


class CheckpointStorePort(Protocol):
    def save(self, checkpoint: Mapping[str, Any]) -> str: ...
    def load(self, checkpoint_id: str) -> Mapping[str, Any]: ...


class TelemetryPort(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None: ...
