from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from .vault import PersonaVault, _atomic_json


MIN_SCORE = 0
MAX_SCORE = 100
DEFAULT_SCORE = 50


class BehaviorEvent(StrEnum):
    DISCOVERY_ANSWERED = "discovery_answered"
    DISCOVERY_SKIPPED = "discovery_skipped"
    DISCOVERY_IGNORED = "discovery_ignored"
    VOLUNTARY_PREFERENCE = "voluntary_preference"
    VOLUNTARY_GOAL = "voluntary_goal"
    USEFUL_CORRECTION = "useful_correction"
    CONTEXT_CONTINUED = "context_continued"

    MEMORY_CONFIRMED = "memory_confirmed"
    CONSISTENT_OBSERVATION = "consistent_observation"
    RETRIEVAL_HELPED = "retrieval_helped"
    MEMORY_CORRECTED = "memory_corrected"
    CONTRADICTION = "contradiction"
    PREFERENCE_CHANGED = "preference_changed"
    MEMORY_EXPIRED = "memory_expired"


ENGAGEMENT_DELTA: dict[BehaviorEvent, int] = {
    BehaviorEvent.DISCOVERY_ANSWERED: +5,
    BehaviorEvent.DISCOVERY_SKIPPED: -5,
    BehaviorEvent.DISCOVERY_IGNORED: -2,
    BehaviorEvent.VOLUNTARY_PREFERENCE: +4,
    BehaviorEvent.VOLUNTARY_GOAL: +3,
    BehaviorEvent.USEFUL_CORRECTION: +2,
    BehaviorEvent.CONTEXT_CONTINUED: +1,
}

STABILITY_DELTA: dict[BehaviorEvent, int] = {
    BehaviorEvent.MEMORY_CONFIRMED: +6,
    BehaviorEvent.CONSISTENT_OBSERVATION: +2,
    BehaviorEvent.RETRIEVAL_HELPED: +1,
    BehaviorEvent.MEMORY_CORRECTED: -8,
    BehaviorEvent.CONTRADICTION: -10,
    BehaviorEvent.PREFERENCE_CHANGED: -5,
    BehaviorEvent.MEMORY_EXPIRED: -2,
}


@dataclass(frozen=True, slots=True)
class BehaviorScores:
    engagement: int = DEFAULT_SCORE
    profile_stability: int = DEFAULT_SCORE
    events: int = 0
    last_event: str = ""
    updated_at: str | None = None


def _clamp(value: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, int(value)))


class BehaviorLedger:
    """Reversible local-only operational scores.

    These scores are not personality traits, are not model context, are not
    exported as participant persona, and never grant tools/authority.
    """

    def __init__(self, vault: PersonaVault):
        self.vault = vault

    def _path(self, person_key: str) -> Path:
        return self.vault.ensure(person_key) / "security" / "behavior.json"

    def read(self, person_key: str) -> BehaviorScores:
        path = self._path(person_key)
        if not path.is_file():
            return BehaviorScores()
        import json
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return BehaviorScores(
            engagement=_clamp(data.get("engagement", DEFAULT_SCORE)),
            profile_stability=_clamp(data.get("profile_stability", DEFAULT_SCORE)),
            events=max(0, int(data.get("events", 0))),
            last_event=str(data.get("last_event", ""))[:80],
            updated_at=data.get("updated_at"),
        )

    def apply(self, person_key: str, event: BehaviorEvent) -> BehaviorScores:
        current = self.read(person_key)
        updated = BehaviorScores(
            engagement=_clamp(current.engagement + ENGAGEMENT_DELTA.get(event, 0)),
            profile_stability=_clamp(
                current.profile_stability + STABILITY_DELTA.get(event, 0)
            ),
            events=current.events + 1,
            last_event=event.value,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(self._path(person_key), {
            "engagement": updated.engagement,
            "profile_stability": updated.profile_stability,
            "events": updated.events,
            "last_event": updated.last_event,
            "updated_at": updated.updated_at,
            "schema": "bossman.pit.behavior/1",
        })
        return updated


def discovery_threshold_adjustment(engagement: int) -> float:
    """Positive value asks less; negative value asks a little more."""
    value = _clamp(engagement)
    if value >= 75:
        return -0.04
    if value >= 60:
        return -0.02
    if value <= 20:
        return +0.12
    if value <= 35:
        return +0.07
    return 0.0


def memory_confidence_floor(profile_stability: int) -> float:
    """Low stability means Bossman must demand stronger evidence before recall."""
    value = _clamp(profile_stability)
    if value >= 80:
        return 0.25
    if value >= 60:
        return 0.35
    if value >= 40:
        return 0.50
    if value >= 20:
        return 0.65
    return 0.75
