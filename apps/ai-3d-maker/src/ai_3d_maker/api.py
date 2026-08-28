"""HTTP skin over `control.ControlPlane`.

Every route delegates to the control object; there is no business logic here.
FastAPI is an optional runtime dependency — the app is fully usable through
`ControlPlane` and the CLI without it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .control import ControlPlane
from .errors import Ai3dError, JobNotFoundError


class JobPayload(BaseModel):
    kind: str | None = None
    job_id: str | None = None
    spec: dict | None = None
    source_stl: str | None = None
    source_units: str = "mm"
    scale: float = 1.0
    auto_orient: bool = True
    place_on_bed: bool = True
    drop_small_components: bool = False
    slice: bool = False
    slicer_settings: dict[str, Any] = Field(default_factory=dict)
    calibrated_tolerance_mm: float | None = None
    scale_to_fit: bool = False
    wait: bool = True


class GcodePayload(BaseModel):
    gcode: str


class ConfirmPayload(BaseModel):
    job_id: str
    action: str = "transfer_to_media"
    artifact: str | None = None
    confirmation: str = ""
    transport: str | None = None


def build_app(control: ControlPlane | None = None) -> FastAPI:
    plane = control or ControlPlane()
    app = FastAPI(title="AI 3D Maker", version=__version__)
    app.state.control = plane

    def _error(exc: Ai3dError, status: int = 400) -> JSONResponse:
        return JSONResponse(status_code=status, content=exc.as_dict())

    @app.get("/health")
    async def health():
        return plane.health()

    @app.get("/capabilities")
    async def capabilities():
        return plane.capabilities()

    @app.get("/metrics")
    async def metrics():
        return plane.metrics()

    @app.get("/api/profile")
    async def profile():
        return plane.profile.as_dict()

    @app.post("/api/jobs")
    async def create_job(payload: JobPayload):
        try:
            return await plane.jobs_create(payload.model_dump())
        except Ai3dError as exc:
            return _error(exc)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/jobs")
    async def list_jobs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), status: str | None = None):
        return plane.jobs_list(limit=limit, offset=offset, status=status)

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        try:
            return plane.jobs_status(job_id)
        except JobNotFoundError as exc:
            return _error(exc, 404)
        except Ai3dError as exc:
            return _error(exc)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        try:
            return plane.jobs_cancel(job_id)
        except JobNotFoundError as exc:
            return _error(exc, 404)
        except Ai3dError as exc:
            return _error(exc)

    @app.get("/api/jobs/{job_id}/artifacts")
    async def artifacts(job_id: str):
        try:
            return plane.artifacts_list(job_id)
        except JobNotFoundError as exc:
            return _error(exc, 404)
        except Ai3dError as exc:
            return _error(exc)

    @app.get("/api/jobs/{job_id}/artifacts/{name}")
    async def artifact(job_id: str, name: str):
        try:
            path = plane.artifact_path(job_id, name)
        except JobNotFoundError as exc:
            return _error(exc, 404)
        except Ai3dError as exc:
            return _error(exc)
        return FileResponse(path, filename=path.name)

    @app.get("/api/jobs/{job_id}/confirmation")
    async def confirmation(job_id: str, artifact: str | None = None):
        try:
            return plane.confirmation_for(job_id, artifact)
        except JobNotFoundError as exc:
            return _error(exc, 404)
        except Ai3dError as exc:
            return _error(exc)

    @app.post("/api/gcode/scan")
    async def gcode_scan(payload: GcodePayload):
        return plane.gcode_scan(payload.gcode)

    @app.post("/api/printer/confirm")
    async def printer_confirm(payload: ConfirmPayload):
        try:
            return plane.printer_confirm(payload.model_dump())
        except JobNotFoundError as exc:
            return _error(exc, 404)
        except Ai3dError as exc:
            return _error(exc)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
