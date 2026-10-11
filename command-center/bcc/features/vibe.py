"""Vibe Studio (owner 2026-10-11): natural-language request -> small static web app.

  GET  /vibe/status                       ladder tiers, queue, STOP, long-run ledger
  POST /vibe/jobs                         queue a build/modify request (privacy: default|local_only)
  GET  /vibe/jobs, /jobs/{id}             history / one job
  GET  /vibe/jobs/{id}/events?after=N     live step log (cursor polling)
  POST /vibe/jobs/{id}/cancel | /retry
  GET  /vibe/apps, /apps/{slug}           apps and their versions
  GET  /vibe/apps/{slug}/versions/{n}/preview   sandbox preview document (strict CSP header)
  GET  /vibe/apps/{slug}/diff?from=&to=   unified diff between two versions
  POST /vibe/apps/{slug}/rollback         move the current pointer (nothing is deleted)
  POST /vibe/apps/{slug}/promote          ask the owner to approve promotion into the apps list
  POST /vibe/apps/{slug}/promote/finish   complete it once the approval is granted
  GET  /vibe/promoted/{slug}/             the promoted static page (same CSP)
  GET  /vibe/long-run                     per-provider daily caps / spend / refusals
  POST /vibe/stop | /vibe/resume          write / clear the autonomy STOP file

Everything here is owner-only (no PIT participants).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import long_run
from ..vibe import VibeError, VibeService
from ..vibe.generator import RegistryLadder
from ..vibe.safety import MAX_REQUEST_CHARS
from . import Feature

router = APIRouter(prefix="/vibe", tags=["vibe"])


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(max_length=MAX_REQUEST_CHARS)
    title: str | None = Field(default=None, max_length=80)
    slug: str | None = Field(default=None, max_length=40)
    base_version: int | None = Field(default=None, ge=1)
    privacy: str = Field(default="default", max_length=16)


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


def _svc(request: Request) -> VibeService:
    return request.app.state.svc.vibe


def _wrap(fn):
    try:
        return fn()
    except VibeError as exc:
        raise HTTPException(exc.status, exc.detail) from None


async def _await(coro):
    try:
        return await coro
    except VibeError as exc:
        raise HTTPException(exc.status, exc.detail) from None


def _html(pair: tuple[str, str]) -> HTMLResponse:
    html, csp = pair
    return HTMLResponse(html, headers={
        "Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin"})


@router.get("/status")
async def status(request: Request):
    svc = _svc(request)
    tiers: dict[str, list[str]] = {"local": [], "free": [], "paid": []}
    try:
        for c in await svc.ladder.candidates(local_only=False):
            tiers[c.tier].append(c.model)
    except Exception as exc:  # noqa: BLE001 - shown to the owner, never fatal
        tiers["error"] = [f"{type(exc).__name__}: {str(exc)[:160]}"]
    live = [j for j in svc.list() if j["status"] in ("queued", "planning", "generating", "checking")]
    return {"ladder": tiers, "queue": {"live": len(live)}, "stop": svc.stop_reason() or None,
            "long_run": long_run.LongRunLedger().report(days=1)}


@router.post("/jobs", status_code=202)
async def create_job(body: JobIn, request: Request):
    return _wrap(lambda: _svc(request).create(body.model_dump()))


@router.get("/jobs")
async def jobs(request: Request):
    return {"jobs": _svc(request).list()}


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request):
    return _wrap(lambda: _svc(request).get(job_id))


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, after: int = 0):
    return _wrap(lambda: _svc(request).events(job_id, max(0, after)))


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    return await _await(_svc(request).cancel(job_id))


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: str, request: Request):
    return _wrap(lambda: _svc(request).retry(job_id))


@router.get("/apps")
async def apps(request: Request):
    return {"apps": _svc(request).apps()}


@router.get("/apps/{slug}")
async def app_meta(slug: str, request: Request):
    return _wrap(lambda: _svc(request).app(slug))


@router.get("/apps/{slug}/versions/{n}/preview")
async def preview(slug: str, n: int, request: Request):
    return _html(_wrap(lambda: _svc(request).preview_html(slug, n)))


@router.get("/apps/{slug}/diff")
async def diff(slug: str, request: Request, from_: int = Query(1, alias="from"), to: int = 2):
    return _wrap(lambda: _svc(request).diff(slug, from_, to))


@router.post("/apps/{slug}/rollback")
async def rollback(slug: str, body: VersionIn, request: Request):
    return _wrap(lambda: _svc(request).rollback(slug, body.version))


@router.post("/apps/{slug}/promote")
async def promote(slug: str, body: VersionIn, request: Request):
    return await _await(_svc(request).request_promotion(slug, body.version))


@router.post("/apps/{slug}/promote/finish")
async def promote_finish(slug: str, request: Request):
    return await _await(_svc(request).finish_promotion(slug))


@router.get("/promoted/{slug}/")
async def promoted(slug: str, request: Request):
    return _html(_wrap(lambda: _svc(request).promoted_html(slug)))


@router.get("/long-run")
async def long_run_report():
    return long_run.LongRunLedger().report(days=7)


@router.post("/stop")
async def stop():
    return {"stop": long_run.request_stop("owner STOP from Vibe Studio", by="owner")}


@router.post("/resume")
async def resume():
    return {"cleared": long_run.clear_stop()}


async def setup(svc):
    svc.vibe = VibeService(svc.settings.data_dir, ladder=RegistryLadder(svc.registry),
                           stop_check=long_run.stop_reason, bus=svc.bus, approvals=svc.approvals)
    await svc.vibe.resume()


async def tick(svc):
    vibe = getattr(svc, "vibe", None)
    if vibe is not None:
        vibe.kick()


FEATURE = Feature(name="vibe", router=router, setup=setup, tick=tick, tick_seconds=5.0)
