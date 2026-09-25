from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Sensitivity(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET_FORBIDDEN = "secret_forbidden"


class EvidenceKind(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"


@dataclass(slots=True)
class ConsentState:
    memory_enabled: bool = False
    raw_history_enabled: bool = False
    sensitive_memory_enabled: bool = False
    remote_processing_enabled: bool = False
    remote_personalization_enabled: bool = False
    training_use_enabled: bool = False
    discovery_enabled: bool = False
    version: str = "pit-consent/1"
    accepted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryCandidate:
    id: str
    category: str
    key: str
    value: Any
    confidence: float
    evidence_kind: EvidenceKind
    sensitivity: Sensitivity
    source_message_id: str
    source_model: str = ""
    source_episode_id: str | None = None
    observed_at: str | None = None
    ingested_at: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    observation_count: int = 1
    retrieval_count: int = 0
    correction_count: int = 0
    contradiction_count: int = 0
    supersedes: str | None = None
    duplicate_of: str | None = None
    ttl_seconds: int | None = None
    utility_score: float = 0.0
    novelty_score: float = 0.0
    retention_label: str = "UNKNOWN"
    extraction_version: str = "pit-extractor/1"
    tags: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.id or not self.category or not self.key:
            raise ValueError("memory candidate requires id/category/key")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be in [0,1]")
        if not self.source_message_id:
            raise ValueError("memory candidate requires provenance")

    def durable_allowed(self, consent: ConsentState) -> bool:
        self.validate()
        if not consent.memory_enabled:
            return False
        if self.sensitivity == Sensitivity.SECRET_FORBIDDEN:
            return False
        if self.sensitivity == Sensitivity.SENSITIVE:
            return bool(consent.sensitive_memory_enabled)
        return True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_kind"] = self.evidence_kind.value
        data["sensitivity"] = self.sensitivity.value
        return data


# These categories may be used for the current answer, but durable inference is
# disabled unless the participant separately opts in to sensitive memory.
SENSITIVE_CATEGORIES = frozenset({
    "health",
    "sexual_life",
    "precise_location",
    "religion",
    "ethnicity",
    "political_preference",
    "biometric",
    "financial_account",
})
