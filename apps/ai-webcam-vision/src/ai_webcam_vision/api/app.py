"""HTTP control contract.

BOSSMAN (or anything else) drives this workload through: health, capabilities,
jobs.create / jobs.status / jobs.cancel, artifacts.list and metrics. The
application imports nothing from the control plane, and the control plane needs
no knowledge of the internals beyond this contract.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Settings
from ..errors import (
    BaselineMissing,
    CaptureTimeout,
    DependencyMissing,
    PrivacyDenied,
    VisionError,
)
from ..logging_setup import configure_logging, get_logger
from ..runtime.jobs import UnknownJobType
from ..runtime.service import CONTRACT_VERSION, VisionService

log = get_logger("api")

STATUS_BY_CODE = {
    "dependency_missing": 503,
    "capture_failed": 502,
    "capture_timeout": 504,
    "baseline_missing": 409,
    "privacy_denied": 403,
    "egress_blocked": 403,
    "config_error": 400,
}

_PAGE = """<!doctype html>
<title>AI WebCam Vision</title>
<style>body{font:14px/1.5 system-ui;margin:2rem;max-width:52rem}code{background:#eee;padding:.1rem .3rem}</style>
<h1>AI WebCam Vision</h1>
<p>Workload service. Control contract:</p>
<ul>
<li><code>GET /api/v1/health</code></li>
<li><code>GET /api/v1/capabilities</code></li>
<li><code>POST /api/v1/jobs</code> &mdash; {"type":"probe|baseline|sample|observe|snapshot"}</li>
<li><code>GET /api/v1/jobs/{job_id}</code></li>
<li><code>POST /api/v1/jobs/{job_id}/cancel</code></li>
<li><code>GET /api/v1/artifacts</code></li>
<li><code>GET /api/v1/metrics</code></li>
<li><code>POST /hooks/motion</code></li>
</ul>
<p>No live video is served by this application by design.</p>
"""


def _error_response(exc: VisionError) -> JSONResponse:
    status = STATUS_BY_CODE.get(exc.code, 500)
    return JSONResponse(status_code=status, content={"error": exc.safe_message, "code": exc.code})


def build_app(settings: Settings | None = None, service: VisionService | None = None) -> FastAPI:
    settings = settings or (service.settings if service else Settings.from_env())
    configure_logging(settings.log_level, settings.log_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or VisionService(settings)
        await app.state.service.start()
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(
        title="AI WebCam Vision",
        version=CONTRACT_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
    )

    def get_service(request: Request) -> VisionService:
        return request.app.state.service

    async def authorise(authorization: str | None = Header(default=None)) -> None:
        """Bearer auth, active whenever AWV_API_TOKEN is configured."""
        expected = settings.api_token
        if not expected:
            return
        provided = ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        import hmac

        if not hmac.compare_digest(provided, expected.reveal()):
            raise HTTPException(status_code=401, detail="unauthorised")

    @app.exception_handler(VisionError)
    async def _vision_error_handler(request: Request, exc: VisionError):  # noqa: ARG001
        return _error_response(exc)

    # ------------------------------------------------------------- surface
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _PAGE

    @app.get("/healthz")
    async def healthz(svc: VisionService = Depends(get_service)) -> dict:
        health = svc.health()
        return {"status": health["status"], "app": health["app"]}

    @app.get("/api/v1/health", dependencies=[Depends(authorise)])
    async def health(svc: VisionService = Depends(get_service)) -> dict:
        return svc.health()

    @app.get("/api/v1/capabilities", dependencies=[Depends(authorise)])
    async def capabilities(svc: VisionService = Depends(get_service)) -> dict:
        return svc.capabilities()

    @app.get("/api/v1/metrics", dependencies=[Depends(authorise)])
    async def metrics(svc: VisionService = Depends(get_service)) -> dict:
        return svc.metrics()

    @app.post("/api/v1/jobs", dependencies=[Depends(authorise)])
    async def create_job(request: Request, svc: VisionService = Depends(get_service)) -> Any:
        payload = await _json_body(request)
        job_type = str(payload.get("type", "")).strip()
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(status_code=400, detail="params must be an object")
        try:
            job = svc.jobs.create(job_type, params)
        except UnknownJobType as exc:
            raise HTTPException(status_code=400, detail=exc.safe_message) from None
        return JSONResponse(status_code=202, content=job.to_dict())

    @app.get("/api/v1/jobs", dependencies=[Depends(authorise)])
    async def list_jobs(limit: int = 50, svc: VisionService = Depends(get_service)) -> dict:
        return {"jobs": [job.to_dict() for job in svc.jobs.list(limit=limit)]}

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(authorise)])
    async def job_status(job_id: str, svc: VisionService = Depends(get_service)) -> dict:
        job = svc.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict()

    @app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(authorise)])
    async def cancel_job(job_id: str, svc: VisionService = Depends(get_service)) -> dict:
        job = svc.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        cancelled = await svc.jobs.cancel(job_id)
        return {"cancelled": cancelled, "job": job.to_dict()}

    @app.get("/api/v1/artifacts", dependencies=[Depends(authorise)])
    async def artifacts(job_id: str | None = None, limit: int = 100,
                        svc: VisionService = Depends(get_service)) -> dict:
        return {"artifacts": svc.store.list_artifacts(job_id=job_id, limit=limit)}

    @app.get("/api/v1/rooms/{room_id}/metrics/today", dependencies=[Depends(authorise)])
    async def room_metrics(room_id: str, svc: VisionService = Depends(get_service)) -> dict:
        return svc.room_metrics_today(room_id)

    @app.post("/hooks/motion", dependencies=[Depends(authorise)])
    async def motion_hook(request: Request, svc: VisionService = Depends(get_service)) -> dict:
        payload = await _json_body(request)
        source = str(payload.get("source") or "edge/webhook")[:80]
        svc.motion.trigger(source)
        return {"ok": True, "motion": svc.motion.state().to_dict()}

    return app


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(status_code=413, detail="request body too large")
    if not raw:
        return {}
    import json

    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return payload


__all__ = ["build_app", "BaselineMissing", "CaptureTimeout", "DependencyMissing", "PrivacyDenied"]
