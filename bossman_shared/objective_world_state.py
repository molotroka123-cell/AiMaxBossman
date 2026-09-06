"""Scope-partitioned world state: verified fact storage, not model belief.

A world fact here is something an observer actually measured, carrying its
source, its scope, when it was observed, how long that reading stays valid and a
provenance reference back to the observation that produced it. Nothing in this
module infers, summarises or believes. `MODEL_TEXT != PROOF`, and equally
`bookkeeping rows are not world-state proof`: a row written by a scheduler, a
mission journal or a model transcript is not admissible here, because only a
verified observation may be turned into a `WorldFact` by the caller.

Invariants carried by the projection rather than by caller discipline:

* A read is three-valued. FRESH returns the fact; STALE and MISSING return an
  explicit UNKNOWN and never the last known value. There is no None-as-healthy
  and no silently stale answer, because "we do not know" must never read as
  "everything is fine".
* Time only moves forward. A fact observed in the future is refused, and an
  older observation never overwrites a newer one, so a reordered or replayed
  event stream cannot regress the projection.
* Scopes are hard partitions. A fact ingested into scope A is unreadable from
  scope B; there is no global namespace to leak across.
* Storage is bounded per scope with deterministic oldest-first eviction, so an
  event flood costs a fixed amount of memory.

Deliberate non-goals. No persistence, no reconciliation across hosts, no
freshness scheduler, no authority: a FRESH fact is evidence for a projection, and
never by itself an authorization to act.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

STATUSES = frozenset({"FRESH", "STALE", "MISSING"})
DEFAULT_MAX_FACTS_PER_SCOPE = 4096


class WorldStateError(ValueError):
    """The projection refused a fact; a refusal is never a fact about the world."""


class _Unknown:
    """Explicit absence of knowledge. Deliberately not falsy and not None.

    Truth-testing raises instead of quietly reading as False, because the entire
    point of this sentinel is that callers must branch on it rather than let an
    unknown world state slip through an `if fact:` check as "not a problem".
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        raise TypeError("UNKNOWN has no truth value; handle FRESH/STALE/MISSING explicitly")


UNKNOWN = _Unknown()


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip() or "\x00" in value:
        raise WorldStateError(f"{name}: nonempty canonical string required")
    return value


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or type(value) is bool or value != value:
        raise WorldStateError(f"{name}: finite number required")
    if value < 0 or (positive and value == 0):
        raise WorldStateError(f"{name}: {'positive' if positive else 'nonnegative'} value required")
    return float(value)


@dataclass(frozen=True, slots=True)
class WorldFact:
    """One measured fact with the freshness window it is valid within."""

    key: str
    value: Any
    source_ref: str
    scope_id: str
    observed_at: float
    max_age_seconds: float
    provenance_ref: str

    def __post_init__(self) -> None:
        for name in ("key", "source_ref", "scope_id", "provenance_ref"):
            _text(getattr(self, name), name)
        _number(self.observed_at, "observed_at")
        _number(self.max_age_seconds, "max_age_seconds", positive=True)

    def fresh(self, now: float) -> bool:
        """Fresh only inside the window; a reading from the future is not fresh."""
        age = _number(now, "now") - self.observed_at
        return 0 <= age <= self.max_age_seconds


@dataclass(frozen=True, slots=True)
class FactRead:
    """Three-valued read result. The value is reachable only when FRESH."""

    scope_id: str
    key: str
    status: str
    fact: WorldFact | None = None

    @property
    def known(self) -> bool:
        return self.status == "FRESH"

    def value_or_unknown(self) -> Any:
        """The measured value, or the UNKNOWN sentinel — never a stale value."""
        return self.fact.value if self.status == "FRESH" and self.fact is not None else UNKNOWN


class WorldStateProjection:
    """Bounded, scope-partitioned store of verified facts."""

    def __init__(self, *, max_facts_per_scope: int = DEFAULT_MAX_FACTS_PER_SCOPE) -> None:
        if type(max_facts_per_scope) is not int or max_facts_per_scope <= 0:
            raise WorldStateError("max_facts_per_scope must be a positive integer")
        self.max_facts_per_scope = max_facts_per_scope
        self._scopes: dict[str, dict[str, WorldFact]] = {}

    def ingest(self, fact: WorldFact, *, now: float) -> bool:
        """Record a fact if it is not from the future and not older than what we hold.

        Returns whether the projection changed. An out-of-order or replayed event
        is ignored rather than raising: reordering is normal, and the projection's
        job is to refuse the regression, not to refuse the stream. A same-instant
        conflicting reading also loses, so ingest order can never decide a fact.
        """
        if type(fact) is not WorldFact:
            raise WorldStateError("a validated WorldFact is required")
        moment = _number(now, "now")
        if fact.observed_at > moment:
            raise WorldStateError("fact observed in the future; the clock or the source is wrong")
        scope = self._scopes.setdefault(fact.scope_id, {})
        held = scope.get(fact.key)
        if held is not None and fact.observed_at <= held.observed_at:
            return False
        scope[fact.key] = fact
        self._evict(scope)
        return True

    def read(self, scope_id: str, key: str, *, now: float) -> FactRead:
        """Read one fact in one scope. Absent or expired is UNKNOWN, never the old value."""
        _text(scope_id, "scope_id")
        _text(key, "key")
        moment = _number(now, "now")
        fact = self._scopes.get(scope_id, {}).get(key)
        if fact is None:
            return FactRead(scope_id, key, "MISSING")
        if not fact.fresh(moment):
            # The fact is deliberately not returned: a caller that could reach it
            # would eventually read it, and stale evidence is how green lies.
            return FactRead(scope_id, key, "STALE")
        return FactRead(scope_id, key, "FRESH", fact)

    def keys(self, scope_id: str) -> tuple[str, ...]:
        _text(scope_id, "scope_id")
        return tuple(sorted(self._scopes.get(scope_id, {})))

    def fact_count(self, scope_id: str) -> int:
        _text(scope_id, "scope_id")
        return len(self._scopes.get(scope_id, {}))

    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted(self._scopes))

    def _evict(self, scope: dict[str, WorldFact]) -> None:
        """Drop the oldest observations first, breaking ties by key for determinism."""
        while len(scope) > self.max_facts_per_scope:
            victim = min(scope, key=lambda k: (scope[k].observed_at, k))
            del scope[victim]


def fact_from_observation(record: Mapping[str, Any], key: str, *, scope_id: str,
                          max_age_seconds: float, provenance_ref: str) -> WorldFact:
    """Project one value of an evaluate-shaped observation record into a fact.

    The caller supplies scope, freshness window and provenance reference; this
    helper only refuses to invent them. A key the observation did not measure is
    an error, not an UNKNOWN fact: absence of measurement must not be stored as
    though it had been measured.
    """
    if not isinstance(record, Mapping):
        raise WorldStateError("an observation record mapping is required")
    for field in ("source_ref", "observed_at", "values", "scope_id"):
        if field not in record:
            raise WorldStateError(f"observation record missing {field}")
    if record["scope_id"] != scope_id:
        raise WorldStateError("observation scope does not match the target scope")
    values = record["values"]
    if not isinstance(values, Mapping) or key not in values:
        raise WorldStateError(f"observation does not measure {key}")
    return WorldFact(key=key, value=values[key], source_ref=record["source_ref"],
                     scope_id=scope_id, observed_at=record["observed_at"],
                     max_age_seconds=max_age_seconds, provenance_ref=provenance_ref)
