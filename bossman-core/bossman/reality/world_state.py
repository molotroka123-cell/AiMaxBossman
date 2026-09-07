"""V7 World State Graph with explicit provenance and freshness."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Iterable
import time


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    value: Any
    source: str
    observed_at_epoch_s: float
    valid_until_epoch_s: float | None = None
    confidence: float = 1.0
    scope: str = "global"
    revision: int = 1
    invalidation_rule: str | None = None

    def __post_init__(self) -> None:
        if not self.subject or not self.predicate or not self.source:
            raise ValueError("subject, predicate and source are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.subject, self.predicate, self.scope

    def freshness(self, now: float | None = None) -> Freshness:
        now = time.time() if now is None else now
        if self.valid_until_epoch_s is None:
            return Freshness.UNKNOWN
        return Freshness.FRESH if now <= self.valid_until_epoch_s else Freshness.STALE


class WorldStateGraph:
    """Append/revise facts without silently converting unknown/stale into truth."""

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str, str], list[Fact]] = {}

    def observe(self, fact: Fact) -> Fact:
        history = self._facts.setdefault(fact.key, [])
        revision = (history[-1].revision + 1) if history else 1
        stored = replace(fact, revision=revision)
        history.append(stored)
        return stored

    def history(self, subject: str, predicate: str, scope: str = "global") -> tuple[Fact, ...]:
        return tuple(self._facts.get((subject, predicate, scope), ()))

    def current(self, subject: str, predicate: str, scope: str = "global",
                *, require_fresh: bool = False, now: float | None = None) -> Fact | None:
        history = self._facts.get((subject, predicate, scope))
        if not history:
            return None
        fact = history[-1]
        if require_fresh and fact.freshness(now) is not Freshness.FRESH:
            return None
        return fact

    def snapshot(self, *, now: float | None = None) -> tuple[Fact, ...]:
        """Return latest facts, including stale/unknown so callers can see uncertainty."""
        return tuple(history[-1] for history in self._facts.values() if history)

    def reconcile(self, subject: str, predicate: str, scope: str = "global",
                  chooser: Callable[[tuple[Fact, ...]], Fact] | None = None) -> Fact | None:
        """Resolve conflicts explicitly; default refuses to guess between sources."""
        history = self.history(subject, predicate, scope)
        if not history:
            return None
        latest_revision = max(f.revision for f in history)
        candidates = tuple(f for f in history if f.revision == latest_revision)
        if len(candidates) == 1:
            return candidates[0]
        return chooser(candidates) if chooser else None
