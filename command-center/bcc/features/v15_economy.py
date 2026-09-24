"""Bossman 1.5 economy orchestration surface.

Starts the shipped economy runner as a child of the existing Bossman. Jev routes
worker roles; it never grants authority. Trading remains read-only/paper and
paid GLM is enabled only by an explicit bounded owner request.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter(prefix="/v15/economy", tags=["v1.5"])
_ACTIVE: dict[Path, tuple[subprocess.Popen, object]] = {}


class StartBody(BaseModel):
    inbox: str = Field(max_length=1000)
    allow_paid_finalizer: bool = False
    glm_cap_usd: float = Field(default=0.50, gt=0, le=0.50)
    run_ling_scenarios: bool = False


def _root(svc) -> Path:
    path = Path(svc.settings.data_dir) / "v1.5" / "economy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runner() -> Path:
    installed = Path(sys.executable).resolve().parents[1] / "app-support" / "v15_economy_orchestrator.py"
    if installed.is_file():
        return installed
    checkout = Path(__file__).resolve().parents[3] / "tools" / "v15_economy_orchestrator.py"
    if checkout.is_file():
        return checkout
    raise HTTPException(503, {"code": "V15_RUNNER_MISSING"})


def _openrouter_present() -> bool:
    if os.getenv("OPENROUTER_API_KEY"):
        return True
    local = os.getenv("LOCALAPPDATA", "")
    if not local:
        return False
    path = Path(local) / "Bossman" / "secrets" / "openrouter-test.env"
    try:
        return path.is_file() and any(
            line.startswith("OPENROUTER_API_KEY=") and line.split("=", 1)[1].strip()
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    except OSError:
        return False


def _jev_present() -> bool:
    return bool(os.getenv("BOSSMAN_JEV_API_KEY") or os.getenv("TYPESAFE_API_KEY"))


def _pid_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        # Query only: never send a signal to an arbitrary PID on Windows.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, value)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(value, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _state(root: Path) -> dict:
    path = root / "run-state.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {"status": "UNREADABLE"}


async def _reap(key: Path, proc: subprocess.Popen, log) -> None:
    try:
        await asyncio.to_thread(proc.wait)
    finally:
        log.close()
        _ACTIVE.pop(key, None)


@router.get("/status")
async def status(request: Request):
    root = _root(request.app.state.svc)
    saved = _state(root)
    detached_alive = (
        root not in _ACTIVE
        and saved.get("status") in ("RUNNING", "STARTING")
        and _pid_alive(saved.get("pid"))
    )
    payload = {
        "running": root in _ACTIVE or detached_alive,
        "detached_after_backend_restart": detached_alive,
        "root": str(root),
        "openrouter_key_present": _openrouter_present(),
        "jev_key_present": _jev_present(),
        "run": saved or None,
    }
    if saved.get("status") in ("RUNNING", "STARTING") and not payload["running"]:
        payload["reconcile_required"] = True
    return payload


@router.post("/start")
async def start(body: StartBody, request: Request):
    svc = request.app.state.svc
    root = _root(svc)
    if root in _ACTIVE:
        raise HTTPException(409, {"code": "V15_ALREADY_RUNNING"})
    saved = _state(root)
    if saved.get("status") in ("RUNNING", "STARTING"):
        if _pid_alive(saved.get("pid")):
            raise HTTPException(409, {"code": "V15_DETACHED_RUN_ACTIVE",
                                      "run_id": saved.get("run_id")})
        raise HTTPException(409, {"code": "V15_RECONCILE_REQUIRED",
                                  "run_id": saved.get("run_id")})
    inbox = Path(body.inbox).expanduser().resolve()
    if not inbox.is_dir():
        raise HTTPException(422, {"code": "V15_INBOX_MISSING"})
    if not _openrouter_present():
        raise HTTPException(422, {"code": "OPENROUTER_KEY_REQUIRED"})
    if not _jev_present():
        raise HTTPException(422, {"code": "JEV_KEY_REQUIRED"})
    cmd = [
        sys.executable, "-I", str(_runner()), "run",
        "--inbox", str(inbox), "--out", str(root),
        "--glm-cap-usd", str(body.glm_cap_usd),
    ]
    if body.allow_paid_finalizer:
        cmd.append("--allow-paid-finalizer")
    if body.run_ling_scenarios:
        cmd.append("--run-ling-scenarios")
    log = (root / "runner.log").open("ab")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    _ACTIVE[root] = (proc, log)
    task = asyncio.create_task(_reap(root, proc, log), name="bcc-v15-economy-reap")
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {
        "status": "STARTING", "pid": proc.pid, "root": str(root),
        "allow_paid_finalizer": body.allow_paid_finalizer,
        "glm_cap_usd": body.glm_cap_usd,
    }


@router.post("/stop")
async def stop(request: Request):
    root = _root(request.app.state.svc)
    (root / "STOP").write_text("owner stop\n", encoding="utf-8")
    return {"status": "STOP_REQUESTED", "root": str(root)}


@router.post("/reconcile")
async def reconcile(request: Request):
    """Acknowledge a dead pre-restart worker before starting another paid-capable run."""
    root = _root(request.app.state.svc)
    saved = _state(root)
    if not saved:
        return {"status": "NO_PREVIOUS_RUN"}
    if saved.get("status") in ("RUNNING", "STARTING") and _pid_alive(saved.get("pid")):
        raise HTTPException(409, {"code": "V15_RUN_STILL_ACTIVE", "run_id": saved.get("run_id")})
    if saved.get("status") in ("RUNNING", "STARTING"):
        saved["status"] = "INTERRUPTED_RECONCILED"
        saved["reconciled_reason"] = "recorded worker pid is not alive; no external trade/order surface exists"
        (root / "run-state.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": saved.get("status"), "run_id": saved.get("run_id")}


FEATURE = Feature(name="v15_economy", router=router)
