"""Authenticated, preview-first File Commander HTTP contract."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import secrets
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .models import JobCreate, JOB_TYPES
from .service import JobService
from .domain import FileCommander
from .ui import app_shell
from . import auth

APP_ID = "file-commander-mini"


class RootReq(BaseModel):
    root: str


class ApplyReq(BaseModel):
    operations: list[dict] = Field(max_length=256)
    approve: bool = False


class UndoReq(BaseModel):
    approve: bool = False


class RenameReq(RootReq):
    pattern: str = "{stem}"
    replace_spaces: bool = True


class RuleReq(BaseModel):
    name: str
    match_ext: list[str]
    target_dir: str


def _token(state_dir: Path) -> str:
    configured = os.environ.get("BOSSMAN_APP_TOKEN", "").strip()
    if configured:
        if len(configured) < 32:
            raise ValueError("BOSSMAN_APP_TOKEN must contain at least 32 characters")
        return configured
    path = state_dir / "token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink():
            raise ValueError("File Commander token must not be a symlink")
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            raise ValueError("invalid File Commander token file")
        return value
    value = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def build_app():
    api = FastAPI(title="File Commander Mini", version="1.0.0")
    jobs = JobService(APP_ID)
    eng = FileCommander(jobs.store)
    token = _token(jobs.store.dir)

    @api.middleware("http")
    async def authenticated(request: Request, call_next):
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 1024 * 1024:
                return JSONResponse({"detail": "request exceeds 1 MiB"}, status_code=413)
        request._body = bytes(body)
        nonce = None
        if request.headers.get(auth.SIGNATURE):
            target = request.scope["raw_path"].decode("ascii")
            if request.scope.get("query_string"):
                target += "?" + request.scope["query_string"].decode("ascii")
            verified = auth.verify_request(token, request.headers, request.method, target, bytes(body))
            if verified and jobs.store.consume_nonce(*verified):
                nonce = verified[0]
            else:
                return JSONResponse({"detail": "invalid, expired or replayed request proof"}, status_code=401)
        elif request.url.path not in {"/", "/health", "/capabilities"}:
            supplied = request.headers.get("X-Bossman-App-Token", "")
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse({"detail": "AUTH_REQUIRED: open File Commander through Bossman Apps"}, status_code=401)
        response = await call_next(request)
        if nonce is None:
            return response
        content = bytearray()
        async for part in response.body_iterator:
            content.extend(part)
            if len(content) > 8 * 1024 * 1024:
                return JSONResponse({"detail": "response exceeded limit"}, status_code=503)
        headers = dict(response.headers)
        headers[auth.RESPONSE] = auth.response_mac(token, nonce, response.status_code, bytes(content))
        return Response(bytes(content), status_code=response.status_code,
                        headers=headers, background=response.background)

    @api.exception_handler(OSError)
    async def filesystem_error(request, exc):
        status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, FileNotFoundError) else 409
        return JSONResponse({"detail": str(exc), "code": "FILESYSTEM_REFUSED"}, status_code=status)

    @api.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        return JSONResponse({"detail": str(exc), "code": "REFRESH_PREVIEW"}, status_code=409)

    @api.exception_handler(KeyError)
    async def missing_record(request, exc):
        return JSONResponse({"detail": "record not found; refresh current state"}, status_code=404)

    @api.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse({"detail": "application database unavailable", "code": "STORAGE_UNAVAILABLE"}, status_code=503)

    @api.get("/")
    def home():
        return app_shell()

    @api.get("/health")
    def health():
        try:
            with jobs.store.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return JSONResponse({"status": "unhealthy", "app": APP_ID, "db": "unhealthy"}, status_code=503)
        return {"status": "healthy", "app": APP_ID, "version": "1.0.0", "storage": "sqlite",
                "db": "healthy", "files": "configured" if eng.policy.roots() else "NOT_CONFIGURED"}

    @api.get("/capabilities")
    def capabilities():
        return {"standalone": True, "imports_bossman": False, "job_types": JOB_TYPES,
                "features": ["scan", "duplicates", "organize_plan", "safe_apply", "undo"]}

    @api.get("/api/roots")
    def roots():
        return {"roots": [str(root) for root in eng.policy.roots()],
                "status": "configured" if eng.policy.roots() else "NOT_CONFIGURED"}

    @api.get("/metrics")
    def metrics(): return jobs.metrics()

    @api.post("/api/jobs")
    def create(req: JobCreate): return jobs.create(req)

    @api.get("/api/jobs")
    def job_list(): return {"jobs": jobs.list()}

    @api.get("/api/jobs/{jid}")
    def job_get(jid: str): return jobs.get(jid)

    @api.post("/api/jobs/{jid}/cancel")
    def job_cancel(jid: str): return jobs.cancel(jid)

    @api.get("/api/jobs/{jid}/artifacts")
    def artifacts(jid: str): return {"job_id": jid, "artifacts": []}

    @api.post("/api/files/scan")
    def scan(req: RootReq): return eng.scan(req.root)

    @api.post("/api/files/duplicates")
    def duplicates(req: RootReq): return eng.duplicates(req.root)

    @api.post("/api/files/organize-plan")
    def plan(req: RootReq): return eng.organize_plan(req.root)

    @api.post("/api/files/apply")
    def apply(req: ApplyReq): return eng.apply(req.operations, req.approve)

    @api.post("/api/files/undo/{batch_id}")
    def undo(batch_id: str, req: UndoReq): return eng.undo(batch_id, req.approve)

    @api.get("/api/files/batches")
    def batches(): return {"batches": [item["value"] for item in jobs.store.kv_list("batches")]}

    @api.post("/api/files/cleanup-summary")
    def cleanup(req: RootReq): return eng.cleanup_summary(req.root)

    @api.post("/api/files/rename-plan")
    def rename(req: RenameReq): return eng.rename_plan(req.root, req.pattern, req.replace_spaces)

    @api.post("/api/rules")
    def add_rule(req: RuleReq): return eng.save_rule(req.name, req.match_ext, req.target_dir)

    @api.get("/api/rules")
    def rules(): return {"rules": [item["value"] for item in jobs.store.kv_list("rules")]}

    @api.post("/api/files/rule-plan")
    def rule_plan(req: RootReq): return eng.rule_plan(req.root)

    @api.post("/api/files/project-groups")
    def groups(req: RootReq): return eng.project_groups(req.root)

    @api.get("/api/audit")
    def audit(limit: int = 100): return {"events": jobs.store.audit_list(limit)}

    return api


app = build_app()
