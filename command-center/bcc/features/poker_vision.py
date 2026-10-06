"""Poker Vision inside Bossman: ONE backend (the poker-vision app process) behind the page, the CLI and Jeff.

This feature is a thin, loopback-only proxy plus lifecycle glue. It owns no vision logic. Rules it enforces:
  * only the app registered in apps/poker-vision is called, only on 127.0.0.1, only a fixed set of operations;
  * STOP is always forwarded, and if the app does not answer, the process is stopped through the existing apps control;
  * acting is possible only for the owner's own Poker Train; the page cannot ask for more than the app allows.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import Feature
from . import apps as apps_feature
from . import apps_control
from . import capability_tree as tree

router = APIRouter(prefix="/poker-vision", tags=["poker-vision"])
APP_ID = "poker-vision"
TREE_NODE = "pv"
DEFAULT_PORT = 8931
TIMEOUT = httpx.Timeout(20.0, connect=2.0)


def _port() -> int:
    app_dir = apps_control.find_app_dir(APP_ID)
    raw = (apps_feature._load(app_dir / "app.manifest.yaml") if app_dir else None) or {}
    port = raw.get("default_port")
    return int(port) if isinstance(port, int) else DEFAULT_PORT


def _base() -> str:
    override = os.environ.get("BOSSMAN_POKER_VISION_URL")        # tests only; must still be loopback
    if override:
        if not override.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise HTTPException(500, {"code": "PV_BAD_OVERRIDE", "message": "loopback only"})
        return override.rstrip("/")
    return f"http://127.0.0.1:{_port()}"


async def _call(method: str, path: str, body: dict | None = None, raw: bool = False):
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False) as client:
        try:
            r = await client.request(method, _base() + path, json=body)
        except httpx.HTTPError as exc:
            raise HTTPException(503, {"code": "PV_SERVICE_DOWN", "message": f"сервис Poker Vision не отвечает: {type(exc).__name__}",
                                      "hint": "запустите его кнопкой «Запустить сервис» (политика приложений должна это разрешать)"}) from exc
    if raw:
        if r.status_code >= 400:
            raise HTTPException(r.status_code, {"code": "PV_NO_FRAME"})
        return r
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text[:200]
        raise HTTPException(r.status_code, detail)
    return r.json()


class SessionBody(BaseModel):
    mode: str = Field(pattern=r"^(replay|trainer|window)$")
    adapter: str = Field(default="poker_train", pattern=r"^[a-z_]{1,32}$")
    path: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=300)
    act: bool = False
    window: dict | None = None
    interval_s: float = Field(default=0.0, ge=0.0, le=5.0)
    bootstrap: str | None = Field(default=None, pattern=r"^cash_nl(2|5|10|25)$")
    max_hands: int = Field(default=40, ge=1, le=200)


@router.get("/status")
async def status(request: Request):
    svc = request.app.state.svc
    proc = apps_control.process_info(APP_ID, svc.settings.data_dir)
    try:
        app = await _call("GET", "/api/v1/status")
        up = True
    except HTTPException:
        app, up = None, False
    return {"service_up": up, "process": proc, "session": app, "app_id": APP_ID,
            "control_policy": await apps_control.policy(svc)}


@router.post("/service/start")
async def service_start(request: Request):
    svc = request.app.state.svc
    pol = await apps_control.policy(svc)
    if not pol["enabled"]:
        raise HTTPException(409, {"code": "APPS_CONTROL_OFF", "message": "управление приложениями выключено политикой владельца", "hint": pol.get("hint")})
    return await apps_control.start_app(APP_ID, svc.settings.data_dir, svc=svc)


@router.get("/capabilities")
async def capabilities():
    return await _call("GET", "/api/v1/capabilities")


@router.post("/session")
async def session(body: SessionBody):
    return await _call("POST", "/api/v1/session", body.model_dump())


class CalibBody(BaseModel):
    adapter: str = Field(default="ton_poker", pattern=r"^[a-z_]{1,32}$")
    labelled_dir: str = Field(max_length=1000)
    heldout_dir: str = Field(max_length=1000)
    rois: dict


@router.post("/calibrate")
async def calibrate(body: CalibBody):
    """Calibrate a NEW layout on labelled frames and verify it on held-out frames; only a passing profile becomes usable."""
    return await _call("POST", "/api/v1/calibrate", body.model_dump())


@router.post("/stop")
async def stop(request: Request):
    """STOP always wins: ask the app; if it does not answer, stop its process through the apps control."""
    svc = request.app.state.svc
    try:
        out = await _call("POST", "/api/v1/stop")
        return {"stopped": True, "via": "app", "status": out}
    except HTTPException:
        try:
            res = await apps_control.stop_app(APP_ID, svc.settings.data_dir, svc=svc)
            return {"stopped": True, "via": "process", "result": res}
        except Exception as exc:  # noqa: BLE001
            return {"stopped": not apps_control.process_info(APP_ID, svc.settings.data_dir).get("running"), "via": "none", "error": str(exc)[:200]}


@router.get("/state")
async def state():
    return await _call("GET", "/api/v1/state")


@router.get("/history")
async def history():
    return await _call("GET", "/api/v1/history")


@router.get("/labelqueue")
async def labelqueue():
    return await _call("GET", "/api/v1/labelqueue")


@router.get("/overlay.png")
async def overlay():
    r = await _call("GET", "/api/v1/overlay.png", raw=True)
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "no-store"})


class TreeSync(BaseModel):
    task: str = Field(max_length=300)
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    run: str = Field(max_length=200)
    metrics: dict = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    state: str = Field(default="working", pattern=r"^(note|working|blocked|done)$")


@router.post("/tree-sync")
async def tree_sync(body: TreeSync, request: Request):
    """Write the current task / SHA / run / metrics / blockers into the capability-tree owner journal (existing mechanism)."""
    svc = request.app.state.svc
    if TREE_NODE not in {n["id"] for n in tree._seed()["nodes"]}:
        raise HTTPException(404, {"code": "CAPABILITY_NODE_NOT_FOUND", "node": TREE_NODE})
    text = json.dumps({"task": body.task, "sha": body.sha, "run": body.run, "metrics": body.metrics, "blockers": body.blockers},
                      ensure_ascii=False)[:3900]
    path = tree._tree_dir(svc) / "owner-notes.json"
    with tree._NOTES_LOCK:
        notes = tree._read(path, {})
        notes[TREE_NODE] = {"text": text, "state": body.state, "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
        tree._atomic(path, notes)
    return {"saved": True, "node_id": TREE_NODE}


FEATURE = Feature(name="poker_vision", router=router)
