"""Resource leases API (Bossman 2.5 T3 / M2): ``/api/resources/state|lease`` over the governor's lease ledger.

The arbiter itself lives in ``tools/resource_governor/leases.py`` (stdlib, shared with the out-of-process governor that
runs expiry, the memory guard and restart recovery). This module only exposes it to the owner: every route is
owner-only (a scoped agent token or a participant header is refused), every ledger call runs in a worker thread (SQLite
and the Ollama unload hook never block the event loop). ``/api/governor/*`` is the AI behaviour governor - not this.

The ledger file is ``<data dir>/one-bossman/resource-leases.db``, the same one the governor process uses; settings come
from the ``leases`` section of ``one-bossman/governor.json``. Nothing here starts or stops a process.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import Feature

router = APIRouter()
_LEASE_ID = re.compile(r"^[0-9a-f]{32}$")
_MODULE_NAME = "bossman_resource_leases"


class LeaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumer: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    priority: str = Field(min_length=1, max_length=32)
    holder: str = Field(default="", max_length=120)
    idempotency_key: str | None = Field(default=None, max_length=200)
    ttl_s: float | None = Field(default=None, ge=10, le=86400)
    pid: int | None = Field(default=None, ge=1)
    proc_create_time: float | None = None


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: int = Field(ge=1)


class ProcessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: int = Field(ge=1)
    pid: int = Field(ge=1)
    proc_create_time: float


class MeasurementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumer: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    peak_gb: float = Field(gt=0, lt=1024)
    config_hash: str = Field(default="", max_length=128)
    cold: bool = False


def _module_path() -> Path | None:
    env = os.environ.get("BOSSMAN_RESOURCE_GOVERNOR_DIR")
    candidates = [Path(env) / "leases.py"] if env else []
    candidates.append(Path(__file__).resolve().parents[3] / "tools" / "resource_governor" / "leases.py")
    return next((p for p in candidates if p.is_file()), None)


def _load_module() -> Any:
    mod = sys.modules.get(_MODULE_NAME)
    if mod is not None:
        return mod
    path = _module_path()
    if path is None:
        raise HTTPException(503, {"code": "governor_module_missing",
                                  "message": "tools/resource_governor/leases.py не найден рядом с бэкендом "
                                             "(задайте BOSSMAN_RESOURCE_GOVERNOR_DIR)"})
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(_MODULE_NAME, None)
        raise
    return mod


def _governor_cfg(data_dir: Path) -> dict:
    try:
        raw = json.loads((data_dir / "one-bossman" / "governor.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _available_gb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:  # noqa: BLE001 - unknown reading: the ledger check still applies
        return None


def _build_ledger(data_dir: Path) -> Any:
    mod = _load_module()
    gcfg = _governor_cfg(data_dir)
    lcfg = dict(gcfg.get("leases")) if isinstance(gcfg.get("leases"), dict) else {}
    keep = gcfg.get("ollama_keep_models")
    if isinstance(keep, list):
        lcfg["keep_models"] = [m for m in keep if isinstance(m, str)] + list(lcfg.get("keep_models") or [])
    ollama = ((gcfg.get("paths") or {}).get("ollama_url") if isinstance(gcfg.get("paths"), dict) else None) \
        or "http://127.0.0.1:11434"
    return mod.LeaseLedger(data_dir / "one-bossman" / mod.DB_NAME, lcfg, mem=_available_gb,
                           unload=mod.OllamaUnloader(str(ollama)))


async def _ledger(request: Request) -> Any:
    svc = request.app.state.svc
    led = getattr(svc, "resource_leases", None)
    if led is None:
        led = await asyncio.to_thread(_build_ledger, Path(svc.settings.data_dir))
        svc.resource_leases = led
    return led


def _require_owner(request: Request) -> None:
    """Owner only, like the other owner features: the role comes from the verified session/token, never a header."""
    principal = getattr(request.state, "principal", None)
    if principal is None or getattr(principal, "role", None) != "owner":
        raise HTTPException(403, {"code": "OWNER_CREDENTIAL_REQUIRED",
                                  "message": "ресурсы GPU/памяти распределяет только владелец"})
    for header in ("x-participant", "x-bcc-participant"):
        value = request.headers.get(header)
        if value is not None and value.strip() != "owner":
            raise HTTPException(403, {"code": "owner_only", "message": "участник не может управлять ресурсами"})


async def _call(request: Request, fn_name: str, *args: Any, **kw: Any) -> Any:
    _require_owner(request)
    led = await _ledger(request)
    mod = _load_module()
    try:
        return await asyncio.to_thread(getattr(led, fn_name), *args, **kw)
    except mod.LeaseError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None


def _lease_id(lease_id: str) -> str:
    if not _LEASE_ID.match(lease_id):
        raise HTTPException(422, {"code": "bad_lease_id", "message": "id аренды — 32 hex-символа"})
    return lease_id


@router.get("/resources/state")
async def resources_state(request: Request):
    return await _call(request, "state")


@router.post("/resources/lease")
async def resources_lease(body: LeaseIn, request: Request):
    out = await _call(request, "request", body.consumer, body.priority, body.holder,
                      idempotency_key=body.idempotency_key, ttl_s=body.ttl_s, pid=body.pid,
                      proc_create_time=body.proc_create_time)
    status = {"granted": 200, "queued": 202, "refused": 422}.get(out.get("state"), 200)
    return JSONResponse(out, status_code=status)


@router.get("/resources/lease/{lease_id}")
async def resources_lease_get(lease_id: str, request: Request):
    out = await _call(request, "get", _lease_id(lease_id))
    out.pop("token", None)
    out.pop("env", None)
    return out


@router.post("/resources/lease/{lease_id}/heartbeat")
async def resources_heartbeat(lease_id: str, body: TokenIn, request: Request):
    return await _call(request, "heartbeat", _lease_id(lease_id), body.token)


@router.post("/resources/lease/{lease_id}/process")
async def resources_attach(lease_id: str, body: ProcessIn, request: Request):
    return await _call(request, "attach_process", _lease_id(lease_id), body.token, body.pid, body.proc_create_time)


@router.delete("/resources/lease/{lease_id}")
async def resources_release(lease_id: str, request: Request, token: int | None = None, force: bool = False):
    return await _call(request, "release", _lease_id(lease_id), token, force=force)


@router.post("/resources/measurement")
async def resources_measurement(body: MeasurementIn, request: Request):
    return await _call(request, "record_measurement", body.consumer, body.peak_gb, body.config_hash, body.cold)


FEATURE = Feature(name="resource_leases", router=router)
