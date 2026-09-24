"""Minimal bitemporal knowledge records for Bossnet 1.6."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Fact:
    fact_id: str
    subject: str
    predicate: str
    obj: str
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    evidence_ref: str
    confidence: float

    def visible_at(self, world_time: datetime, knowledge_time: datetime) -> bool:
        return (self.recorded_at <= knowledge_time and self.valid_from <= world_time
                and (self.valid_to is None or world_time < self.valid_to)
                and 0 <= self.confidence <= 1)


def historical_view(facts: list[Fact], *, world_time: datetime, knowledge_time: datetime) -> list[Fact]:
    return [f for f in facts if f.visible_at(world_time,knowledge_time)]
