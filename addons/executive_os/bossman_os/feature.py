"""Opt-in Executive OS routes through the existing authenticated BCC feature API."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from bcc.features import Feature

router = APIRouter(prefix="/executive-os", tags=["executive-os"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(Input):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    action: Literal["artifact.write", "artifact.verify"]
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=65536)


class Submit(Input):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    project: str = Field(default="default", min_length=1, max_length=80,
                         pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    steps: list[Step] = Field(min_length=1, max_length=64)
    context_roots: list[str] = Field(default_factory=list, max_length=8)


class Propose(Input):
    objective: str = Field(min_length=1, max_length=3000)
    project: str = Field(default="default", min_length=1, max_length=80,
                         pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    context_roots: list[str] = Field(default_factory=list, max_length=8)


class Evaluation(Input):
    suite_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    phase: Literal["baseline", "candidate"]
    cases: dict[str, str] = Field(min_length=1, max_length=100)


class Recover(Input):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _runtime(request: Request) -> Any:
    runtime = getattr(request.app.state.svc, "executive_os", None)
    if runtime is None:
        raise HTTPException(503, detail="Executive OS is not enabled for this BCC instance")
    return runtime


def _http_error(exc: Exception) -> HTTPException:
    # Runtime details can contain private source paths or content. Keep them
    # out of HTTP errors; request IDs and stored mission state support diagnosis.
    if isinstance(exc, PermissionError):
        return HTTPException(403, detail="Executive OS policy denied this request")
    if isinstance(exc, ValueError):
        return HTTPException(400, detail="Invalid Executive OS request")
    return HTTPException(409, detail="Executive OS operation could not complete")


async def _call(request: Request, method: str, *args: Any) -> Any:
    runtime = _runtime(request)
    try:
        return await asyncio.to_thread(getattr(runtime, method), *args)
    except (ValueError, RuntimeError, OSError) as exc:
        raise _http_error(exc) from None


@router.get("/status")
async def status(request: Request) -> dict:
    runtime = getattr(request.app.state.svc, "executive_os", None)
    if runtime is None:
        return {"enabled": False, "scope": "managed_missions_only",
                "existing_bcc_tasks": "unmanaged", "runtime": None}
    return {"enabled": True, "scope": "managed_missions_only",
            "existing_bcc_tasks": "unmanaged", "runtime": await _call(request, "status")}


@router.post("/missions", status_code=201)
async def submit(body: Submit, request: Request) -> Any:
    return await _call(request, "submit", body.model_dump())


@router.post("/missions/{mission_id}/run")
async def run(mission_id: str, request: Request) -> Any:
    return await _call(request, "run", mission_id)


@router.get("/missions/{mission_id}")
async def snapshot(mission_id: str, request: Request) -> Any:
    return await _call(request, "snapshot", mission_id)


@router.post("/evaluate")
async def evaluate(body: Evaluation, request: Request) -> Any:
    return await _call(request, "evaluate", body.model_dump())


@router.post("/recover")
async def recover(body: Recover, request: Request) -> Any:
    return await _call(request, "recover", body.id)


@router.post("/propose")
async def propose(body: Propose, request: Request) -> Any:
    runtime = _runtime(request)
    try:
        return await runtime.propose(body.model_dump())
    except (ValueError, RuntimeError, OSError) as exc:
        raise _http_error(exc) from None


async def setup(svc: Any) -> None:
    config = getattr(svc, "executive_os_config", None)
    svc.executive_os = None
    if config is None:
        return
    factory = config.get("runtime_factory")
    if factory is None:
        from .runtime import Runtime
        factory = Runtime
    svc.executive_os = await asyncio.to_thread(factory, Path(config["state_root"]),
                                              Path(config["artifact_root"]))


FEATURE = Feature(name="executive_os", router=router, setup=setup)
