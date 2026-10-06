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
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..rave.engine import RaveError, RaveService
from ..rave.pool import PoolError
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


class PoolAccountIn(BaseModel):
    tool: str = Field(max_length=16)
    label: str = Field(min_length=1, max_length=40)
    profile_dir: str | None = Field(default=None, max_length=500)
    use_default: bool = False


class PoolEnableIn(BaseModel):
    approval_id: int | None = None


class PoolEnabledIn(BaseModel):
    enabled: bool


class PruneIn(BaseModel):
    older_than_days: float = Field(ge=0, le=3650)
    dry_run: bool = True                      # nothing is deleted unless the caller says dry_run=false
    include_blocked: bool = False


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
    except (RaveError, PoolError) as exc:
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


CONNECTORS_TTL = 15.0           # the CLIs' status commands take a moment: one answer serves a burst of page renders


async def _soft(coro, fallback: dict) -> dict:
    """A status command that can not run (bossman-core missing, CLI refused to start) is a state to show."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        return {**fallback, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def connector_states(svc: Any, *, refresh: bool = False) -> dict:
    """What each CLI connector would run under, from each CLI's own status commands (login) and
    `--version`. Cached ~15 s per backend; `refresh=True` asks the CLIs again."""
    hit = getattr(svc, "_rave_connector_states", None)
    if hit and not refresh and time.monotonic() - hit[0] < CONNECTORS_TTL:
        return hit[1]
    from ..rave import connectors as c
    claude, codex, cver, xver = await asyncio.gather(
        _soft(c.claude_login(), {"installed": None, "logged_in": False}),
        _soft(c.codex_login(), {"installed": None, "logged_in": False}),
        _soft(c.claude_version(), {"installed": None, "version": None, "ok": False,
                                   "min": ".".join(map(str, c.MIN_CLAUDE_VERSION))}),
        _soft(c.codex_version(), {"installed": None, "version": None}))
    claude = {**claude, "version": cver.get("version"), "version_ok": bool(cver.get("ok")),
              "version_min": cver.get("min"),
              "version_problem": c._version_problem(cver) if cver.get("installed") else ""}
    codex = {**codex, "version": xver.get("version")}
    states = {"claude": claude, "codex": codex}
    svc._rave_connector_states = (time.monotonic(), states)
    return states


@router.get("/rave/connectors")
async def connectors_status(request: Request, refresh: bool = False):
    """What each connector would run under, from each CLI's own status command."""
    from ..rave import connectors as c
    rave = _svc(request)
    states = await connector_states(request.app.state.svc, refresh=refresh)
    return {"mock": {"ready": True, "auth": "none"},
            "local": {"endpoint": c.DEFAULT_LOCAL_ENDPOINT, "default_model": c.DEFAULT_LOCAL_MODEL,
                      "auth": "local (no login)"},
            "claude": {**states["claude"], "optin": rave.optin_state().get("claude"),
                       "sources": list(c.CLAUDE_SOURCES)},
            "codex": {**states["codex"], "optin": rave.optin_state().get("codex"),
                      "sources": list(c.CODEX_SOURCES)},
            "api_key_path": {"enabled": c.api_key_allowed(c.AgentSpec("claude", "x", None, {"auth": "api_key"})),
                             "how": f"{c.API_KEY_OPT_ENV}=1 + agent param auth=api_key (off by default)"},
            "pool": rave.pool.summary()}


@router.post("/rave/stop-all")
async def stop_all(request: Request):
    """STOP every rave; the answer is the confirmed count ({stopped, stopped_count, remaining, ok})."""
    return await _call(_svc(request).stop_all())


@router.post("/rave/prune")
async def prune_raves(body: PruneIn, request: Request):
    """Remove the workspaces of finished raves older than N days. Dry run unless dry_run=false."""
    return await _call(_svc(request).prune(body.older_than_days, dry_run=body.dry_run,
                                           include_unfinished=body.include_blocked))


# ---- account pool (the owner's OWN accounts; off by default). Declared before /rave/{rid} on purpose.


@router.get("/rave/pool")
async def pool_state(request: Request):
    """Pool state for the Rave page: accounts (state, limit window, login step), opt-in, switch journal.
    No secret is stored or returned."""
    return _svc(request).pool.view()


@router.post("/rave/pool/accounts")
async def pool_add_account(body: PoolAccountIn, request: Request):
    rave = _svc(request)
    account = await _call(rave.pool.add_account(body.tool, body.label, profile_dir=body.profile_dir,
                                                use_default=body.use_default))
    await rave.event_global("pool_changed", account=account["id"])
    return {"account": account, "pool": rave.pool.view()}


@router.delete("/rave/pool/accounts/{account_id}")
async def pool_remove_account(account_id: str, request: Request):
    rave = _svc(request)
    await _call(rave.pool.remove_account(account_id))
    await rave.event_global("pool_changed", account=account_id)
    return rave.pool.view()


@router.post("/rave/pool/accounts/{account_id}/check")
async def pool_check_account(account_id: str, request: Request):
    """Ask that account's own CLI (in its own profile) whether it is logged in: status command only."""
    rave = _svc(request)
    account = await _call(rave.pool.check(account_id))
    await rave.event_global("pool_changed", account=account_id)
    return {"account": account, "pool": rave.pool.view()}


@router.post("/rave/pool/accounts/{account_id}/clear-limit")
async def pool_clear_limit(account_id: str, request: Request):
    rave = _svc(request)
    await _call(rave.pool.clear_limit(account_id))
    await rave.event_global("pool_changed", account=account_id)
    return rave.pool.view()


@router.post("/rave/pool/accounts/{account_id}/enabled")
async def pool_account_enabled(account_id: str, body: PoolEnabledIn, request: Request):
    rave = _svc(request)
    await _call(rave.pool.set_account_enabled(account_id, body.enabled))
    await rave.event_global("pool_changed", account=account_id)
    return rave.pool.view()


@router.post("/rave/pool/enable")
async def pool_enable(body: PoolEnableIn, request: Request):
    """Owner opt-in. First call -> 202 WAIT_APPROVAL (a normal approval `rave_pool_optin` listing the exact
    accounts and the terms-of-service note); after the owner approves it, the same call with {approval_id}."""
    status, payload = await _call(_svc(request).pool_enable(body.approval_id))
    return JSONResponse(payload, status_code=status)


@router.post("/rave/pool/disable")
async def pool_disable(request: Request):
    rave = _svc(request)
    view = await _call(rave.pool.disable())
    await rave.event_global("pool_changed")
    return view


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
    getter: asyncio.Future | None = None       # one pending q.get(); always cancelled on the way out
    try:
        while True:
            if not svc.bus.is_subscribed(q):      # dropped as a lagging reader: subscribe again
                if getter is not None and not getter.done():
                    getter.cancel()
                getter = None
                q = svc.bus.subscribe()
            if getter is None:
                getter = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait({getter}, timeout=5.0)
            if not done:
                continue                          # nothing yet: re-check the subscription
            msg = getter.result()
            getter = None
            if isinstance(msg, dict) and msg.get("kind") == "owner.stop_all":
                with contextlib.suppress(Exception):
                    await rave.stop_all()
    except asyncio.CancelledError:
        await rave.shutdown()
        raise
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
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
