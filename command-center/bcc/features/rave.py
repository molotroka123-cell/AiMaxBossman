"""Agentic Rave API (Bossman 1.9, workstream G) — `/api/rave/*`.

One prompt → several agents, each in its own isolated git clone; per-agent
and rave-wide pause / resume / STOP; conflicts detected with every version
kept; the owner's global STOP (`owner.stop_all`) stops every rave. The logic
lives in bcc.rave.engine; this module is the HTTP surface + startup recovery.
Design: docs/v1.9/AGENTIC_RAVE.md.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..rave.engine import RaveError, RaveService
from . import Feature

router = APIRouter()


class RaveIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    agents: list[str] = Field(min_length=1, max_length=16)
    repo: str | None = Field(default=None, max_length=1000)
    allow: list[str] = Field(default_factory=list, max_length=64)
    test: str | None = Field(default=None, max_length=1000)


class ControlIn(BaseModel):
    agent: str | None = Field(default=None, max_length=32)


class ApplyIn(BaseModel):
    approval_id: int | None = None


def service(svc: Any) -> RaveService:
    rave = getattr(svc, "rave", None)
    if rave is None:
        rave = svc.rave = RaveService(svc)
    return rave


def _svc(request: Request) -> RaveService:
    return service(request.app.state.svc)


async def _call(coro):
    try:
        return await coro
    except RaveError as exc:
        raise HTTPException(exc.status, exc.detail) from None


def _sync(fn, *args):
    try:
        return fn(*args)
    except RaveError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.post("/rave")
async def create_rave(body: RaveIn, request: Request):
    rave = _svc(request)
    return await _call(rave.create(prompt=body.prompt, agents=body.agents, repo=body.repo,
                                   allow=body.allow, test=body.test))


@router.get("/rave")
async def list_raves(request: Request):
    return {"items": _svc(request).list()}


@router.get("/rave/connectors")
async def connectors_status(request: Request):
    """What each connector would run under, from each CLI's own status command."""
    from ..rave import connectors as c
    claude, codex = await asyncio.gather(c.claude_login(), c.codex_login())
    rave = _svc(request)
    return {"mock": {"ready": True, "auth": "none"},
            "local": {"endpoint": c.DEFAULT_LOCAL_ENDPOINT, "default_model": c.DEFAULT_LOCAL_MODEL,
                      "auth": "local (no login)"},
            "claude": {**claude, "optin": rave.optin_state().get("claude"), "sources": list(c.CLAUDE_SOURCES)},
            "codex": {**codex, "optin": rave.optin_state().get("codex"), "sources": list(c.CODEX_SOURCES)},
            "api_key_path": {"enabled": c.api_key_allowed(c.AgentSpec("claude", "x", None, {"auth": "api_key"})),
                             "how": f"{c.API_KEY_OPT_ENV}=1 + agent param auth=api_key (off by default)"}}


@router.post("/rave/stop-all")
async def stop_all(request: Request):
    return await _call(_svc(request).stop_all())


@router.get("/rave/{rid}")
async def get_rave(rid: str, request: Request):
    rave = _svc(request)
    return rave.view(_sync(rave.load, rid))


@router.get("/rave/{rid}/events")
async def rave_events(rid: str, request: Request, after: int = 0):
    return _sync(_svc(request).events, rid, max(0, after))


@router.post("/rave/{rid}/pause")
async def pause_rave(rid: str, body: ControlIn, request: Request):
    return await _call(_svc(request).pause(rid, body.agent))


@router.post("/rave/{rid}/resume")
async def resume_rave(rid: str, body: ControlIn, request: Request):
    return await _call(_svc(request).resume(rid, body.agent))


@router.post("/rave/{rid}/stop")
async def stop_rave(rid: str, body: ControlIn, request: Request):
    return await _call(_svc(request).stop(rid, body.agent))


@router.get("/rave/{rid}/agents/{name}/diff")
async def agent_diff(rid: str, name: str, request: Request):
    return await _call(_svc(request).diff(rid, name))


@router.get("/rave/{rid}/conflicts")
async def rave_conflicts(rid: str, request: Request):
    rave = _svc(request)
    rec = _sync(rave.load, rid)
    return {"id": rid, "conflicts": rec.get("conflicts") or []}


@router.post("/rave/{rid}/agents/{name}/apply")
async def apply_agent(rid: str, name: str, body: ApplyIn, request: Request):
    """Owner decision: copy one agent's result into the project working tree.
    Needs a normal Bossman approval each time; never commits or pushes."""
    status, payload = await _call(_svc(request).apply(rid, name, body.approval_id))
    return JSONResponse(payload, status_code=status)


async def _watch_owner_stop(svc: Any, rave: RaveService) -> None:
    """Owner global STOP (`bossman stop --all`, palette, Telegram) → STOP every rave.
    Cancelled when the backend stops: then child processes are killed and the
    durable state is left for recovery (agents come back paused)."""
    q = svc.bus.subscribe()
    try:
        while True:
            if not svc.bus.is_subscribed(q):      # dropped as a lagging reader: subscribe again
                q = svc.bus.subscribe()
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(msg, dict) and msg.get("kind") == "owner.stop_all":
                with contextlib.suppress(Exception):
                    await rave.stop_all()
    except asyncio.CancelledError:
        await rave.shutdown()
        raise
    finally:
        svc.bus.unsubscribe(q)


async def setup(svc: Any) -> None:
    rave = service(svc)
    recovered = await rave.recover()
    if recovered:
        with contextlib.suppress(Exception):
            await svc.bus.emit("rave.recovered_after_restart", raves={k: len(v) for k, v in recovered.items()})
    task = asyncio.create_task(_watch_owner_stop(svc, rave), name="bcc-rave-owner-stop")
    svc._tasks.append(task)


FEATURE = Feature(name="rave", router=router, setup=setup)
