"""Owner-facing surface for the V7 reality core.

Everything here is read-only or advisory. There is deliberately no endpoint
that executes a compiled mission, follows a shadow recommendation, or promotes
one: those are exactly the steps that would turn an advisory subsystem into a
second authorization path. Compiling shows the owner what a contract WOULD be;
acting on it still goes through the existing task, permission and approval
machinery.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from .. import qa_relay  # noqa: F401  — keeps feature import order stable
from ..reality import (compiler, media_routing, observers, strategy,
                       telemetry, world)
from . import Feature

router = APIRouter(tags=["reality"])


@router.post("/reality/compile")
async def compile_mission(request: Request):
    """Compile an owner intent into a Mission IR candidate.

    Returns the digest and the validated contract, or the exact reason it could
    not be one. It does not create a task and grants nothing."""
    body = await request.json()
    result = compiler.compile_intent(body if isinstance(body, dict) else {})
    if result.status == compiler.REFUSED:
        raise HTTPException(422, {"code": "MISSION_IR_REFUSED", **result.to_dict()})
    return result.to_dict()


@router.get("/reality/observe")
async def observe(request: Request, repo: str | None = None):
    """One pass of every observation adapter.

    An adapter that could not measure reports `available: false` with a reason
    rather than a zero — "we could not look" and "we looked and found nothing"
    are different findings."""
    svc = request.app.state.svc
    readings = await observers.observe_all(svc, repo=repo)
    return {"observations": [o.to_dict() for o in readings],
            "available": sum(1 for o in readings if o.available),
            "unavailable": sum(1 for o in readings if not o.available)}


@router.get("/reality/world")
async def world_state(request: Request, refresh: bool = True, repo: str | None = None):
    """What the system currently believes, and how fresh each belief is.

    A value is present only under a FRESH status: STALE, MISSING and CONTESTED
    carry none, so nothing downstream can read a value the projection refused to
    vouch for. `refresh=false` inspects the graph without observing, which is how
    you watch a fact age out rather than being renewed under you.

    Read-only by construction. There is no route that writes a fact: a world
    state anyone can post to is a belief store, and beliefs are not evidence."""
    svc = request.app.state.svc
    pass_result = await world.refresh(svc, repo=repo) if refresh else None
    body = world.belief(svc)
    if pass_result is not None:
        body["last_pass"] = pass_result
    return body


def memory_reading(svc, *, now: float | None = None) -> strategy.MemoryReading:
    """Free memory from the world state, or an explicit non-measurement.

    Read through the projection rather than sampling directly, so freshness is
    honoured: a STALE reading is not a smaller number, it is the absence of a
    current one, and a path that needs memory must not be sized against it.
    """
    read = world.projection(svc).read(world.AMBIENT_SCOPE, "process.host",
                                      now=time.time() if now is None else now)
    if read.status != "FRESH" or read.fact is None:
        return strategy.MemoryReading(None, f"process.host {read.status.lower()}")
    value = read.fact.value
    available = value.get("ram_available_mb") if isinstance(value, dict) else None
    if not isinstance(available, (int, float)) or isinstance(available, bool):
        return strategy.MemoryReading(None, "process.host has no ram_available_mb")
    return strategy.MemoryReading(float(available), "observer:process")


@router.get("/reality/strategies")
async def strategies(request: Request, deterministic: bool = False,
                     unknown_facts: int = 0,
                     small_model_mb: float = 0.0, large_model_mb: float = 0.0):
    """The shadow router's ranking, with every utility term kept separately.

    Advisory by construction: `shadow_only` is in the response, and nothing in
    this codebase routes from it.

    `small_model_mb` / `large_model_mb` declare what a path would need
    resident. Declared, a path is checked against the measured free memory and
    refused when it does not fit — or when nothing measured it, because an
    unmeasured budget cannot be shown to hold a 70 GB model."""
    from .. import model_health as mh
    svc = request.app.state.svc
    import sqlalchemy as sa
    from ..db import models as models_t
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(models_t.c.id, models_t.c.kind,
                                          models_t.c.health))).fetchall()
    health: dict[str, mh.HealthRecord] = {}
    for row in rows:
        record = mh.HealthRecord.from_dict(row[2])
        bucket = "large" if str(row[1] or "") == "cloud" else "small"
        # Best measured model of each bucket represents it: a bucket is not
        # healthier than its healthiest member, and not worse than its worst.
        if bucket not in health or record.rank_key() < health[bucket].rank_key():
            health[bucket] = record
    memory = memory_reading(svc)
    candidates = strategy.generate_strategies(
        deterministic_available=bool(deterministic), model_health=health,
        unknown_facts=max(0, int(unknown_facts)), memory=memory,
        model_memory_mb={"small_model": max(0.0, float(small_model_mb)),
                         "large_model": max(0.0, float(large_model_mb))})
    decision = strategy.shadow_route(candidates, permissions=[])
    body = decision.to_dict()
    body["memory"] = {"available_mb": memory.available_mb, "source": memory.source,
                      "measured": memory.measured}
    return body


@router.get("/reality/generation-media-observations")
async def generation_media_observations(
        request: Request, refresh: bool = True,
        browser: str = media_routing.UNKNOWN,
        local_generator: str = media_routing.UNKNOWN,
        buffer: str | None = None):
    """What the media generator may know about this machine before it routes.

    Observations only. No route is chosen here and none could be: the ranking
    rules live in the generator service, in one copy, and a second copy in the
    control plane is exactly the duplicate truth store the architecture asks
    not to build.

    A value appears only under a FRESH fact. STALE and MISSING are published as
    "not measured" rather than as a smaller number, because the consumer closes
    a route on an unmeasured fact — which is how an unmeasured memory budget
    stops being able to authorise loading a 70 GB model."""
    svc = request.app.state.svc
    if refresh:
        await world.refresh(svc)
    return media_routing.observations(svc, browser=browser,
                                      local_generator=local_generator,
                                      buffer=buffer)


@router.get("/reality/shadow")
async def shadow(request: Request, limit: int = 100):
    """What the shadow router recommended and what production actually did."""
    svc = request.app.state.svc
    return {"scoreboard": await telemetry.scoreboard(svc),
            "history": await telemetry.history(svc, limit=limit)}


async def _tick(svc) -> None:
    """Keep the graph populated between requests.

    Without this the projection is empty until someone asks, every read is
    instantaneous, and freshness is a property nothing ever exercises. The pass
    is deterministic and cheap — a git command, a psutil sample and three
    queries — and an adapter that fails contributes nothing rather than
    poisoning the graph."""
    await world.refresh(svc)


#: Slower than the observations' own validity windows on purpose: the graph
#: should be able to go STALE. A refresh that always beat every expiry would
#: mean the freshness machinery never actually reports anything.
TICK_SECONDS = 60.0

FEATURE = Feature(name="reality", router=router, tick=_tick, tick_seconds=TICK_SECONDS)
