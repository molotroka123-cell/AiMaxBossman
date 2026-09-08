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
from ..reality import compiler, observers, strategy, telemetry
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


FEATURE = Feature(name="reality", router=router)
