"""Owner-facing surface for the V7 reality core.

Everything here is read-only or advisory. There is deliberately no endpoint
that executes a compiled mission, follows a shadow recommendation, or promotes
one: those are exactly the steps that would turn an advisory subsystem into a
second authorization path. Compiling shows the owner what a contract WOULD be;
acting on it still goes through the existing task, permission and approval
machinery.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import qa_relay  # noqa: F401  — keeps feature import order stable
from ..reality import compiler, observers, strategy, telemetry, world
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


@router.get("/reality/strategies")
async def strategies(request: Request, deterministic: bool = False,
                     unknown_facts: int = 0):
    """The shadow router's ranking, with every utility term kept separately.

    Advisory by construction: `shadow_only` is in the response, and nothing in
    this codebase routes from it."""
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
    candidates = strategy.generate_strategies(
        deterministic_available=bool(deterministic), model_health=health,
        unknown_facts=max(0, int(unknown_facts)))
    decision = strategy.shadow_route(candidates, permissions=[])
    return decision.to_dict()


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
