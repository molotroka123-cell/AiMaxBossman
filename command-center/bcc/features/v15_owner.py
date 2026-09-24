"""Bossman 1.5 owner control surface.

One owner action starts the two independent long-running lanes:
1) read-only Twitch market collection + deterministic analysis + Telegram owner notifications;
2) Bossman self-improvement/YouTube economy bootstrap.

Aster/Codex are not runtime dependencies. They can audit later.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature
from .v15_economy import _jev_present, _openrouter_present, _pid_alive

router = APIRouter(prefix="/v15/owner", tags=["v1.5"])
_SELF_PROC: subprocess.Popen | None = None
_MARKET_PROC: subprocess.Popen | None = None


class StartBody(BaseModel):
    allow_paid_finalizer: bool = True
    glm_cap_usd: float = Field(default=0.50, gt=0, le=0.50)
    market_cadence_s: float = Field(default=15.0, ge=5.0, le=120.0)
    youtube_url: str = Field(default="", max_length=1000)


def _app_root() -> Path:
    installed = Path(sys.executable).resolve().parents[1]
    if (installed / "MANIFEST.json").is_file():
        return installed
    return Path(__file__).resolve().parents[3]


def _support(name: str) -> Path:
    installed = Path(sys.executable).resolve().parents[1] / "app-support" / name
    if installed.is_file():
        return installed
    checkout = Path(__file__).resolve().parents[3] / "tools" / name
    if checkout.is_file():
        return checkout
    raise HTTPException(503, {"code": "V15_SUPPORT_MISSING", "file": name})


def _owner_root(svc) -> Path:
    root = Path(svc.settings.data_dir) / "v1.5" / "owner"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _source_sha() -> str:
    for name in ("BOSSMAN_ACCEPTANCE_SHA", "BOSSMAN_BUILD_SHA", "GITHUB_SHA"):
        value = os.environ.get(name, "").strip().lower()
        if len(value) == 40 and all(c in "0123456789abcdef" for c in value):
            return value
    manifest = _app_root() / "MANIFEST.json"
    try:
        value = str(json.loads(manifest.read_text(encoding="utf-8")).get("source_sha") or "").lower()
    except (OSError, ValueError):
        value = ""
    if len(value) == 40 and all(c in "0123456789abcdef" for c in value):
        return value
    root = _app_root()
    if (root / ".git").exists():
        try:
            p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                               text=True, timeout=10, encoding="utf-8", errors="replace")
            value = p.stdout.strip().lower()
            if p.returncode == 0 and len(value) == 40:
                return value
        except (OSError, subprocess.SubprocessError):
            pass
    return "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _tail_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            rows = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(rows):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _market_root() -> Path:
    from bcc.market.ledger import default_root
    return default_root("k1m6a")


def _market_status() -> dict[str, Any]:
    root = _market_root()
    state = _read_json(root / "reports" / "collector-status.json")
    pid = 0
    try:
        pid = int((root / "collector.pid").read_text().strip())
    except (OSError, ValueError):
        pass
    return {
        "running": bool(pid and _pid_alive(pid)),
        "pid": pid or None,
        "state": state,
        "last_delivery": _tail_json(root / "reports" / "delivery.jsonl"),
        "root": str(root),
    }


def _owner_state(svc) -> dict[str, Any]:
    return _read_json(_owner_root(svc) / "state.json")


def _write_owner_state(svc, body: dict[str, Any]) -> None:
    path = _owner_root(svc) / "state.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _self_status(svc) -> dict[str, Any]:
    state = _owner_state(svc)
    pid = state.get("self_improve_pid")
    work = Path(state["work"]) if isinstance(state.get("work"), str) else None
    return {
        "running": bool(pid and _pid_alive(pid)),
        "pid": pid,
        "work": str(work) if work else None,
        "bootstrap": _read_json(work / "bootstrap-report.json") if work else {},
        "evolution": _read_json(work / "evolution" / "loop-state.json") if work else {},
    }


def _preflight(svc) -> dict[str, Any]:
    source = _source_sha()
    return {
        "source_sha": source,
        "source_proven": source != "unknown",
        "openrouter_key_present": _openrouter_present(),
        "jev_key_present": _jev_present(),
        "self_improve_runner": _support("bossman_15_self_improve.py").is_file(),
        "youtube_batch": _support("youtube_trader_ingest_batch.py").is_file(),
        "market_module": True,
        "telegram_market_delivery": "ENABLED_BY_MARKET_RUN_IF_COMPANION_CONFIG_VALID",
        "aster_required": False,
        "codex_required_for_runtime": False,
    }


@router.get("/status")
async def status(request: Request):
    svc = request.app.state.svc
    return {
        "schema": "bossman.v1.5.owner-status/1",
        "preflight": _preflight(svc),
        "market": _market_status(),
        "self_improvement": _self_status(svc),
        "economy": _read_json(Path(svc.settings.data_dir) / "v1.5" / "economy" / "run-state.json"),
    }


@router.post("/quick-test")
async def quick_test(request: Request):
    svc = request.app.state.svc
    pf = _preflight(svc)
    market = _market_status()
    blockers = []
    for key in ("source_proven", "openrouter_key_present", "jev_key_present",
                "self_improve_runner", "youtube_batch", "market_module"):
        if not pf.get(key):
            blockers.append(key)
    return {
        "status": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "preflight": pf,
        "market_already_running": market["running"],
        "notes": "Quick test does not claim YouTube learning or Twitch extraction quality; live run proves those.",
    }


@router.post("/start")
async def start(body: StartBody, request: Request):
    global _SELF_PROC, _MARKET_PROC
    svc = request.app.state.svc
    pf = _preflight(svc)
    if pf["source_proven"] is not True:
        raise HTTPException(409, {"code": "SOURCE_IDENTITY_UNKNOWN"})

    owner_root = _owner_root(svc)
    market = _market_status()
    if not market["running"]:
        log = (owner_root / "market.log").open("ab")
        _MARKET_PROC = subprocess.Popen(
            [sys.executable, "-m", "bcc.market.collector", "run",
             "--cadence", str(body.market_cadence_s)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )

    self_state = _self_status(svc)
    if self_state["running"]:
        return {"status": "ALREADY_RUNNING", "market": _market_status(),
                "self_improvement": self_state}

    if not pf["openrouter_key_present"]:
        raise HTTPException(422, {"code": "OPENROUTER_KEY_REQUIRED",
                                  "market_started": True})
    if not pf["jev_key_present"]:
        raise HTTPException(422, {"code": "JEV_KEY_REQUIRED",
                                  "market_started": True})

    sha = pf["source_sha"]
    work = Path(svc.settings.data_dir) / "self-improvement" / ("v1.5-" + sha[:12])
    work.mkdir(parents=True, exist_ok=True)
    log = (owner_root / "self-improve.log").open("ab")
    base_url = f"http://127.0.0.1:{svc.settings.port}"
    cmd = [
        sys.executable, "-I", str(_support("bossman_15_self_improve.py")),
        "--data-dir", str(svc.settings.data_dir),
        "--api-url", base_url,
        "start", "--work", str(work),
    ]
    if body.allow_paid_finalizer:
        cmd.append("--allow-glm")
    if body.youtube_url.strip():
        cmd += ["--youtube-url", body.youtube_url.strip()]
    env = dict(os.environ)
    env["BOSSMAN_15_GLM_BUDGET_USD"] = str(body.glm_cap_usd)
    _SELF_PROC = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)

    now = time.time()
    _write_owner_state(svc, {
        "schema": "bossman.v1.5.owner-run/1",
        "source_sha": sha,
        "started_at": now,
        "self_improve_pid": _SELF_PROC.pid,
        "market_pid": _MARKET_PROC.pid if _MARKET_PROC is not None else _market_status().get("pid"),
        "work": str(work),
        "allow_paid_finalizer": body.allow_paid_finalizer,
        "glm_cap_usd": body.glm_cap_usd,
        "market_cadence_s": body.market_cadence_s,
        "aster_required": False,
    })
    return {
        "status": "STARTED",
        "source_sha": sha,
        "market": _market_status(),
        "self_improvement": _self_status(svc),
    }


@router.post("/stop")
async def stop(request: Request):
    global _SELF_PROC
    svc = request.app.state.svc
    owner = _owner_state(svc)
    work = owner.get("work")
    # Market collector checks this durable STOP within at most one current attempt.
    market_root = _market_root()
    market_root.mkdir(parents=True, exist_ok=True)
    (market_root / "STOP").write_text(str(time.time()), encoding="utf-8")
    # Economy child has its own durable STOP.
    economy_root = Path(svc.settings.data_dir) / "v1.5" / "economy"
    economy_root.mkdir(parents=True, exist_ok=True)
    (economy_root / "STOP").write_text("owner stop\n", encoding="utf-8")

    if isinstance(work, str):
        script = _support("bossman_15_self_improve.py")
        cmd = [
            sys.executable, "-I", str(script),
            "--data-dir", str(svc.settings.data_dir),
            "--api-url", f"http://127.0.0.1:{svc.settings.port}",
            "stop", "--work", work,
        ]
        await asyncio.to_thread(
            subprocess.run, cmd, cwd=_app_root(), capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace", check=False)

    if _SELF_PROC is not None and _SELF_PROC.poll() is None:
        _SELF_PROC.terminate()
    return {"status": "STOP_REQUESTED", "market": _market_status(),
            "self_improvement": _self_status(svc)}


FEATURE = Feature(name="v15_owner", router=router)
