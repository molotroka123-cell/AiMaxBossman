"""Direct Generation window API (authenticated like every feature router, under /api).

  GET  /direct-gen/models                 installed photo + video models (kind) with real availability/reason
  GET  /direct-gen/status                 runtime reachability, memory, queue, ASSISTED availability
  POST /direct-gen/jobs                   create a job (mode DIRECT: prompt goes to the model as written)
  GET  /direct-gen/jobs, /jobs/{id}       this participant's history / one job
  POST /direct-gen/jobs/{id}/cancel       STOP (queued or mid-run; interrupts that ComfyUI prompt only)
  POST /direct-gen/jobs/{id}/retry        same raw prompt and SAME seed
  GET  /direct-gen/jobs/{id}/file         the finished video
  POST /direct-gen/assist                 ASSISTED: local Qwen proposes an edit; nothing is generated or loaded
  GET  /direct-gen/faceswap               FaceFusion installed? presets
  POST /direct-gen/swap-jobs              face swap in a video (photos + consent), 16:9 + original audio;
                                          the job shares /jobs/{id}, cancel and file with every other job
  GET  /direct-gen/genjutsu               live constructor: mask engines (availability, licence), LoRA training
                                          status (honestly «не подключено»: there is no training backend)
  POST /direct-gen/genjutsu/masks         region masks (hair/top/bottom/clothes) for one picture + consent;
                                          PNG base64 + areas; nothing is stored. Recolour runs in the browser.
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


class SwapIn(BaseModel):
    """Face swap in a video (FaceFusion, local): the person on 1..5 photos replaces the face(s) in the clip."""
    model_config = ConfigDict(extra="forbid")
    video_b64: str = Field(max_length=410 * 1024 * 1024)
    faces_b64: list[str] = Field(min_length=1, max_length=5)
    preset: str = Field(default="fast", max_length=16)
    frame: str = Field(default="original", max_length=16)
    consent: bool = False


class MasksIn(BaseModel):
    """Masks for the Genjutsu live constructor: one picture (a photo or a video frame) + the person's consent."""
    model_config = ConfigDict(extra="forbid")
    image_b64: str = Field(max_length=48 * 1024 * 1024)
    engine: str | None = Field(default=None, max_length=16)
    consent: bool = False


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


@router.get("/faceswap")
async def faceswap_info(request: Request):
    from ..direct_gen import faceswap
    return faceswap.describe(_svc(request).faceswap_home)


@router.post("/swap-jobs", status_code=202)
async def create_swap(body: SwapIn, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).create_swap(_who(x_participant), body.model_dump()))


@router.get("/genjutsu")
async def genjutsu_info(request: Request):
    return _svc(request).genjutsu_info()


@router.post("/genjutsu/masks")
async def genjutsu_masks(body: MasksIn, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).create_masks(_who(x_participant), body.model_dump()))


@router.post("/assist")
async def assist(body: AssistIn, request: Request, x_participant: str | None = Header(default=None)):
    return await _guard(_svc(request).assist(_who(x_participant), body.prompt))


async def _wrap(fn):
    return fn()


async def setup(svc):
    svc.direct_gen = DirectGenService(svc)


FEATURE = Feature(name="direct_gen", router=router, setup=setup)
