"""Typed quarantined learning episodes compiled from video evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


STATUSES = ("RAW", "UNVERIFIED", "OUTCOME_LABELLED", "PROMOTED", "REJECTED")


@dataclass
class TeacherClaim:
    text: str
    kind: str = "UNKNOWN"  # OBSERVATION|HYPOTHESIS|TRIGGER|INVALIDATION|TARGET|UNKNOWN
    confidence: float = 0.0


@dataclass
class LearningEpisode:
    source_url: str
    video_id: str
    timestamp_s: float
    frame_sha256: str
    extractor_version: str
    observation: dict[str, Any]
    horizons: dict[str, Any] = field(default_factory=dict)
    levels: dict[str, Any] = field(default_factory=dict)
    level_events: dict[str, str] = field(default_factory=dict)
    claims: list[TeacherClaim] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    status: str = "UNVERIFIED"
    verifier_refs: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors = []
        if self.status not in STATUSES:
            errors.append("status")
        if not self.source_url or not self.video_id or self.timestamp_s < 0:
            errors.append("source_identity")
        if len(self.frame_sha256) != 64:
            errors.append("frame_sha256")
        if not self.extractor_version:
            errors.append("extractor_version")
        if self.status == "PROMOTED" and (not self.outcome or not self.verifier_refs):
            errors.append("promotion_without_outcome_and_verifier")
        return errors

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["claims"] = [asdict(c) for c in self.claims]
        return out


def can_promote(ep: LearningEpisode) -> bool:
    """A local model/teacher claim cannot promote itself."""
    return not ep.validate() and bool(ep.outcome) and bool(ep.verifier_refs) and ep.status == "PROMOTED"
