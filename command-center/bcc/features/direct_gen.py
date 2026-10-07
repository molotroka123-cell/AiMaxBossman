"""Direct Generation window API (authenticated like every feature router, under /api).

  GET  /direct-gen/models                 installed photo + video models (kind) with real availability/reason
  GET  /direct-gen/status                 runtime reachability, memory, queue, ASSISTED availability
  POST /direct-gen/jobs                   create a job (mode DIRECT: prompt goes to the model as written)
  GET  /direct-gen/jobs, /jobs/{id}       this participant's history / one job
  POST /direct-gen/jobs/{id}/cancel       STOP (queued or mid-run; interrupts that ComfyUI prompt only)
  POST /direct-gen/jobs/{id}/retry        same raw prompt and SAME seed
  GET  /direct-gen/jobs/{id}/file         the finished video
  POST /direct-gen/assist                 ASSISTED: local Qwen proposes an edit; nothing is generated or loaded
Events: `direct_gen.job` on the shared event bus (status, stage, real progress, elapsed).
The participant comes from the `X-Participant` header ('owner' by default or a 64-hex PIT key);
jobs of one participant are invisible to another.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..direct_gen.service import DirectGenError, DirectGenService
from ..direct_gen.store import valid_participant
from . import Feature

router = APIRouter(prefix="/direct-gen", tags=["direct-gen"])


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(max_length=80)
    prompt: str = Field(max_length=8000)
    negative: str = Field(default="", max_length=8000)
    duration: float = 4
    resolution: str = Field(default="480x320", max_length=16)
    seed: int | None = None
    steps: int | None = Field(default=None, ge=1, le=200)
    mode: str = Field(default="DIRECT", max_length=16)
    assist_id: str | None = Field(default=None, max_length=64)
    assist_choice: str | None = Field(default=None, max_length=16)
    image_b64: str | None = Field(default=None, max_length=48 * 1024 * 1024)
    audio_b64: str | None = Field(default=None, max_length=48 * 1024 * 1024)


class AssistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(max_length=8000)


def _svc(request: Request) -> DirectGenService:
    return request.app.state.svc.direct_gen


def _who(x_participant: str | None) -> str:
    try:
        return valid_participant(x_participant)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_participant", "message": str(exc)}) from None


async def _guard(call):
    try:
        return await call
    except DirectGenError as exc:
        raise HTTPException(exc.status or 500, exc.detail) from None


@router.get("/models")
async def models(request: Request):
    return {"models": _svc(request).models()}


@router.get("/status")
async def status(request: Request):
    return await _svc(request).status()


@router.post("/jobs", status_code=202)
async def create(body: JobIn, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).create(_who(x_participant), body.model_dump()))


@router.get("/jobs")
async def jobs(request: Request, x_participant: str | None = Header(default=None)):
    return {"jobs": _svc(request).list(_who(x_participant))}


@router.get("/jobs/{job_id}")
async def job(job_id: str, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_wrap(lambda: _svc(request).get(_who(x_participant), job_id)))


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).cancel(_who(x_participant), job_id))


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: str, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).retry(_who(x_participant), job_id))


@router.get("/jobs/{job_id}/file")
async def file(job_id: str, request: Request, x_participant: str | None = Header(default=None)):
    svc = _svc(request)
    who = _who(x_participant)
    try:
        path = svc.result_path(who, job_id)
        mime = svc.get(who, job_id)["result"]["mime"]
    except DirectGenError as exc:
        raise HTTPException(exc.status or 500, exc.detail) from None
    return FileResponse(path, media_type=mime, filename="bossman-direct" + path.suffix)


@router.post("/assist")
async def assist(body: AssistIn, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).assist(_who(x_participant), body.prompt))


async def _wrap(fn):
    return fn()


async def setup(svc):
    svc.direct_gen = DirectGenService(svc)


FEATURE = Feature(name="direct_gen", router=router, setup=setup)
