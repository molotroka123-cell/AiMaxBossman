"""The World State Graph as a running thing, not only a contract.

`observers.py` could measure and `bossman_shared.objective_world_state` could
store, and nothing connected them: every observation was computed for one HTTP
response and thrown away. A graph that is empty at runtime has no freshness to
speak of — every read is instantaneous by construction — and the owner has no
way to ask the question the V7 charter puts first: what does Bossman currently
believe, and how sure is that belief?

So this module keeps one projection for the process, refreshes it from the
observation adapters, and reports the belief with its provenance. Three things
it deliberately does not do:

  * It grants nothing. A FRESH fact is evidence for a projection and never an
    authorization; `require_fresh` is the only way to cross an effect boundary
    with one, and it refuses everything that is not FRESH.
  * It accepts no facts from outside. There is no ingest endpoint, because a
    world state anyone can write to is a belief store, not an observation
    store. Facts enter only from the deterministic adapters.
  * It infers nothing. Two observers that disagree stay disagreeing; the
    projection reports CONTESTED rather than picking the newer one.
"""
from __future__ import annotations

import time
from typing import Any

from bossman_shared.objective_world_state import (WorldFact, WorldStateProjection,
                                                  require_fresh as _require_fresh)

from . import observers

#: One scope per process for the ambient system view. Mission-scoped projections
#: are a separate concern; the point of a scope is that facts cannot leak across
#: one, so the ambient view must not share a namespace with mission evidence.
AMBIENT_SCOPE = "bcc.ambient"

#: How the projection is bounded. A key is one fact about the world however many
#: observers reported it; the source cap keeps a misbehaving observer from
#: turning visible disagreement into an unbounded memory channel.
MAX_KEYS = 512
MAX_SOURCES = 8


def projection(svc) -> WorldStateProjection:
    """The process's projection, created on first use."""
    existing = getattr(svc, "world_state", None)
    if existing is None:
        existing = WorldStateProjection(max_facts_per_scope=MAX_KEYS,
                                        max_sources_per_key=MAX_SOURCES)
        svc.world_state = existing
    return existing


async def refresh(svc, *, repo: str | None = None, scope_id: str = AMBIENT_SCOPE,
                  now: float | None = None) -> dict[str, Any]:
    """One observation pass, ingested. Unavailable readings contribute nothing.

    An adapter that could not measure leaves the key alone rather than writing a
    null: the previous fact then ages out on its own schedule and the read turns
    STALE, which is the truth. Overwriting it with "we could not look" would
    destroy the last thing anyone actually measured.
    """
    moment = time.time() if now is None else now
    readings = await observers.observe_all(svc, repo=repo)
    world = projection(svc)
    ingested = 0
    for obs in readings:
        if not obs.available:
            continue
        fact = WorldFact(key=obs.key, value=obs.value,
                         source_ref=f"observer:{obs.source}", scope_id=scope_id,
                         # An adapter that timed its reading in the future would
                         # be refused by the projection; clamp to now so a clock
                         # skew degrades to "measured just now" instead of
                         # taking down the refresh.
                         observed_at=min(obs.observed_at, moment),
                         max_age_seconds=obs.max_age_seconds,
                         provenance_ref=f"bcc.reality.observers.{obs.source}")
        if world.ingest(fact, now=moment):
            ingested += 1
    return {"scope_id": scope_id, "observed": len(readings),
            "available": sum(1 for o in readings if o.available), "ingested": ingested,
            "unavailable": [{"key": o.key, "source": o.source, "reason": o.reason}
                            for o in readings if not o.available]}


def belief(svc, *, scope_id: str = AMBIENT_SCOPE, now: float | None = None) -> dict[str, Any]:
    """What the projection currently holds, with the freshness of each answer.

    A value appears only under a FRESH status. STALE, MISSING and CONTESTED
    carry no value at all, so a consumer of this dictionary cannot accidentally
    read one — the shape enforces what the projection promises.
    """
    moment = time.time() if now is None else now
    world = projection(svc)
    facts = []
    for key in world.keys(scope_id):
        read = world.read(scope_id, key, now=moment)
        row: dict[str, Any] = {"key": key, "status": read.status,
                               "sources": list(read.sources())}
        if read.status == "FRESH" and read.fact is not None:
            row["value"] = read.fact.value
            row["observed_at"] = read.fact.observed_at
            row["expires_at"] = read.fact.observed_at + read.fact.max_age_seconds
            row["provenance"] = read.fact.provenance_ref
        elif read.status == "CONTESTED":
            row["conflicting"] = [{"source": f.source_ref, "value": f.value,
                                   "observed_at": f.observed_at} for f in read.conflicting]
        facts.append(row)
    contested = [f["key"] for f in facts if f["status"] == "CONTESTED"]
    return {"scope_id": scope_id, "at": moment, "facts": facts,
            "fresh": sum(1 for f in facts if f["status"] == "FRESH"),
            "stale": sum(1 for f in facts if f["status"] == "STALE"),
            "contested": contested,
            # Stated rather than implied: a caller that sees an empty list should
            # know whether nothing disagreed or nothing was checked.
            "note": ("each adapter owns its own key, so disagreement appears only "
                     "when a second observer reports a key another already holds")}


def require_fresh(svc, key: str, *, scope_id: str = AMBIENT_SCOPE,
                  now: float | None = None) -> Any:
    """Effect-boundary read: the measured value, or a refusal naming the reason.

    Re-exported at this layer so callers never reach past it into the raw
    projection, where a stale fact would be one attribute access away.
    """
    return _require_fresh(projection(svc), scope_id, key,
                          now=time.time() if now is None else now)
