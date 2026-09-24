"""Unified Bossman 1.5 owner-run API: self-improvement + Twitch collector."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter(prefix="/v15/owner-run", tags=["v1.5"])


class StartBody(BaseModel):
    repo: str | None = Field(default=None, max_length=1000)
    cycles: int = Field(default=8, ge=1, le=20)
    allow_glm: bool = False
    youtube_url: str = Field(default="", max_length=1000)
    cadence: float = Field(default=15.0, ge=5.0, le=120.0)


def _script() -> Path:
    installed = Path(sys.executable).resolve().parents[1] / "app-support" / "bossman_15_owner_run.py"
    if installed.is_file():
        return installed
    checkout = Path(__file__).resolve().parents[3] / "tools" / "bossman_15_owner_run.py"
    if checkout.is_file():
        return checkout
    raise HTTPException(503, {"code": "V15_OWNER_RUNNER_MISSING"})


def _call(svc, command: str, body: StartBody | None = None) -> dict:
    argv = [sys.executable, "-I", str(_script()), "--data-dir", str(svc.settings.data_dir), command]
    if body is not None:
        if body.repo:
            argv += ["--repo", body.repo]
        argv += ["--cycles", str(body.cycles), "--cadence", str(body.cadence)]
        if body.allow_glm:
            argv.append("--allow-glm")
        if body.youtube_url:
            argv += ["--youtube-url", body.youtube_url]
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace", env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, {"code": "V15_OWNER_RUNNER_FAILED",
                                  "message": f"{type(exc).__name__}: {exc}"}) from exc
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise HTTPException(502, {"code": "V15_OWNER_RUNNER_BAD_OUTPUT",
                                  "message": proc.stdout[-1000:]}) from exc
    payload["returncode"] = proc.returncode
    return payload


@router.get("/status")
async def status(request: Request):
    out = _call(request.app.state.svc, "status")
    store = getattr(request.app.state.svc, "owner_input", None)
    out["owner_inputs_pending"] = len(store.pending()) if store is not None else None
    return out


@router.post("/quick-test")
async def quick_test(request: Request):
    svc = request.app.state.svc
    out = _call(svc, "status")
    problems = []
    if not _script().is_file():
        problems.append("runner_missing")
    if not (os.environ.get("BOSSMAN_JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")):
        problems.append("jev_key_missing")
    openrouter = bool(
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("BOSSMAN_OPENROUTER_API_KEY")
        or (Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "secrets" / "openrouter-test.env").is_file()
    )
    if not openrouter:
        problems.append("openrouter_key_missing")
    return {
        "status": "READY" if not problems else "OWNER_REQUIRED",
        "problems": problems,
        "runner": str(_script()),
        "market_trading_execution": "OFF",
        "stable_auto_write": False,
        "current": out,
    }


@router.post("/start")
async def start(body: StartBody, request: Request):
    out = _call(request.app.state.svc, "start", body)
    await request.app.state.svc.bus.emit("v15.owner_run.started",
                                         status=out.get("status"), root=out.get("root"))
    return out


@router.post("/stop")
async def stop(request: Request):
    out = _call(request.app.state.svc, "stop")
    await request.app.state.svc.bus.emit("v15.owner_run.stop_requested", root=out.get("root"))
    return out


FEATURE = Feature(name="v15_owner_run", router=router)
