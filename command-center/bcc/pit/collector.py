from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import (
    ConsentState,
    EvidenceKind,
    MemoryCandidate,
    SENSITIVE_CATEGORIES,
    Sensitivity,
)
from .secret_filter import contains_secret_hint
from .vault import PersonaVault, _append_jsonl


@dataclass(frozen=True, slots=True)
class CollectionResult:
    accepted: int
    rejected: int
    rejected_secret: int
    rejected_sensitive: int


def enforce_sensitivity(candidate: MemoryCandidate) -> MemoryCandidate:
    """Fail toward more protection when extractor labels are too permissive."""
    if contains_secret_hint(str(candidate.value)):
        candidate.sensitivity = Sensitivity.SECRET_FORBIDDEN
    elif candidate.category in SENSITIVE_CATEGORIES and candidate.sensitivity == Sensitivity.NORMAL:
        candidate.sensitivity = Sensitivity.SENSITIVE
    return candidate


class HighRecallCollector:
    """Collection-first memory intake.

    It intentionally keeps low-confidence NORMAL candidates for later sorting,
    while refusing secrets and consent-gated sensitive durable storage.
    """

    def __init__(self, vault: PersonaVault):
        self.vault = vault

    def ingest(self, person_key: str, candidates: Iterable[MemoryCandidate]) -> CollectionResult:
        state = self.vault.consent(person_key)
        accepted = rejected = rejected_secret = rejected_sensitive = 0
        for candidate in candidates:
            candidate = enforce_sensitivity(candidate)
            if candidate.sensitivity == Sensitivity.SECRET_FORBIDDEN:
                rejected += 1
                rejected_secret += 1
                continue
            if candidate.sensitivity == Sensitivity.SENSITIVE and not state.sensitive_memory_enabled:
                rejected += 1
                rejected_sensitive += 1
                continue
            if self.vault.append_candidate(person_key, candidate):
                accepted += 1
            else:
                rejected += 1
        return CollectionResult(accepted, rejected, rejected_secret, rejected_sensitive)

    def record_outcome(
        self,
        person_key: str,
        *,
        candidate_id: str,
        outcome: str,
        useful: bool | None = None,
        retrieved: bool = False,
        corrected: bool = False,
        note: str = "",
    ) -> None:
        """Ground-truth stream for the later garbage-sorter/retention model."""
        target = self.vault.ensure(person_key)
        _append_jsonl(
            target / "memory_outcomes.jsonl",
            {
                "candidate_id": candidate_id,
                "outcome": outcome,
                "useful": useful,
                "retrieved": bool(retrieved),
                "corrected": bool(corrected),
                "note": str(note)[:500],
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "schema": "pit-memory-outcome/1",
            },
        )
