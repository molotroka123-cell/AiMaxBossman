"""Stage 12 — scoped API surface for the private mobile client.

This module EXTENDS Stage 6 `remote_client`; it does not create a second auth
system.  The parent `/remote` router includes this router, so paths below become
`/remote/tasks`, `/remote/approvals`, etc.

Security invariants:
- every data/action endpoint is scope-gated by Stage 6 authentication;
- no route can mutate cloud_policy, agent tools, model provider secrets, or scopes;
- non-admin chat devices see only tasks created by their own device;
- approval payloads are never returned; previews are redacted;
- static PWA assets contain no credentials and are the only unauthenticated paths.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db, events, obs, runner
from ..agents import load_all
from .auth import SCOPE_ADMIN, SCOPE_APPROVE, SCOPE_CHAT, Principal
from .security import authenticate_request, require_scope
from .service import get_service

router = APIRouter()
_APP_DIR = Path(__file__).resolve().parents[2] / "remote-app"
_ASSETS: dict[str, tuple[str, str]] = {
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "remote-core.mjs": ("remote-core.mjs", "text/javascript; charset=utf-8"),
    "styles.css": ("styles.css", "text/css; charset=utf-8"),
    "manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}


class MobileTaskIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    agent: str | None = Field(default=None, max_length=100)


class DecisionIn(BaseModel):
    approve: bool


def _source(principal: Principal) -> str:
    return f"remote:{principal.device_id}"


def _view(row: Any) -> dict[str, Any]:
    data = dict(row)
    allowed = {
        "id", "agent", "source", "text", "status", "result", "error",
        "created_at", "started_at", "finished_at", "updated_at",
    }
    return obs.redact_obj({k: v for k, v in data.items() if k in allowed})


def _approval_view(row: Any) -> dict[str, Any]:
    data = dict(row)
    allowed = {"id", "agent", "kind", "preview", "status", "created_at", "decided_at", "decided_by"}
    out = {k: v for k, v in data.items() if k in allowed}
    if isinstance(out.get("preview"), str):
        # два слоя: секреты (obs) + PII-подобное (Stage 11 sanitizer) — второй redactor не строится
        from ..ai_lab.sanitizer import sanitize_text
        out["preview"] = sanitize_text(obs.redact(out["preview"][:4000]))
    return out


@router.post("/tasks")
async def mobile_create_task(
    body: MobileTaskIn,
    principal: Principal = Depends(require_scope(SCOPE_CHAT)),
):
    """Create a Bossman task without exposing any policy/model mutation knob."""
    if body.agent is not None:
        agents = load_all()
        if body.agent not in agents:
            raise HTTPException(422, f"unknown agent: {body.agent}")
    row = await db.fetchrow(
        "INSERT INTO tasks (agent, source, text) VALUES ($1,$2,$3) RETURNING *",
        body.agent,
        _source(principal),
        body.text,
    )
    await runner.enqueue(row["id"])
    events.emit("task.created", id=row["id"], agent=body.agent, source=_source(principal))
    return _view(row)


@router.get("/tasks")
async def mobile_list_tasks(
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_scope(SCOPE_CHAT)),
):
    """Admin sees all tasks; ordinary chat devices see only their own tasks."""
    is_admin = SCOPE_ADMIN in principal.scopes
    if is_admin and status:
        rows = await db.fetch(
            "SELECT * FROM tasks WHERE status=$1 ORDER BY id DESC LIMIT $2", status, limit
        )
    elif is_admin:
        rows = await db.fetch("SELECT * FROM tasks ORDER BY id DESC LIMIT $1", limit)
    elif status:
        rows = await db.fetch(
            "SELECT * FROM tasks WHERE source=$1 AND status=$2 ORDER BY id DESC LIMIT $3",
            _source(principal), status, limit,
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM tasks WHERE source=$1 ORDER BY id DESC LIMIT $2",
            _source(principal), limit,
        )
    return [_view(r) for r in rows]


@router.get("/tasks/{task_id}")
async def mobile_get_task(
    task_id: int,
    principal: Principal = Depends(require_scope(SCOPE_CHAT)),
):
    if SCOPE_ADMIN in principal.scopes:
        row = await db.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
    else:
        row = await db.fetchrow(
            "SELECT * FROM tasks WHERE id=$1 AND source=$2", task_id, _source(principal)
        )
    if not row:
        raise HTTPException(404, "task not found")
    return _view(row)


@router.get("/approvals")
async def mobile_list_approvals(
    status: str = Query(default="pending", max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_scope(SCOPE_APPROVE)),
):
    del principal
    rows = await db.fetch(
        "SELECT * FROM approvals WHERE status=$1 ORDER BY id DESC LIMIT $2", status, limit
    )
    return [_approval_view(r) for r in rows]


@router.get("/agents")
async def mobile_list_agents(
    principal: Principal = Depends(require_scope(SCOPE_CHAT)),
):
    """Read-only minimal agent catalog; security-sensitive fields are omitted."""
    del principal
    return [
        {"name": a.name, "title": a.title, "model": a.model}
        for a in load_all().values()
    ]


@router.post("/session/logout")
async def mobile_logout(principal: Principal = Depends(authenticate_request)):
    """Revoke the current session. Device tokens are not revoked by logout."""
    if principal.session_id is None:
        return {"ok": True, "revoked": False, "reason": "device-token session"}
    revoked = await get_service().revoke_session(principal.session_id)
    return {"ok": True, "revoked": bool(revoked)}


# Static PWA shell. The shell itself is not secret; all API calls remain gated.
@router.get("/app")
async def mobile_app_index():
    path = _APP_DIR / "index.html"
    if not path.is_file():
        raise HTTPException(404, "Stage 12 app is not installed")
    return FileResponse(path, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.get("/app/{asset}")
async def mobile_app_asset(asset: str):
    item = _ASSETS.get(asset)
    if item is None:
        raise HTTPException(404)
    filename, media_type = item
    path = _APP_DIR / filename
    if not path.is_file():
        raise HTTPException(404)
    cache = "no-store" if filename in {"sw.js", "manifest.webmanifest"} else "public, max-age=3600"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": cache})
