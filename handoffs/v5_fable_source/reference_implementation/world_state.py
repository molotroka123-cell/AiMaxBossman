from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class WorldFact:
    key: str
    value: Any
    source_ref: str
    scope_id: str
    observed_at: float
    max_age_seconds: float
    provenance_ref: str
    def fresh(self, now: float) -> bool:
        return 0 <= now - self.observed_at <= self.max_age_seconds

class WorldStateProjection:
    def __init__(self):
        self._facts = {}
    def ingest(self, fact: WorldFact):
        key = (fact.scope_id, fact.key)
        old = self._facts.get(key)
        if old is None or fact.observed_at >= old.observed_at:
            self._facts[key] = fact
    def read(self, scope_id: str, key: str, *, now: float):
        fact = self._facts.get((scope_id, key))
        return fact if fact is not None and fact.fresh(now) else None
