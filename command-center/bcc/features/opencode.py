"""Feature 07 (часть) — OpenCode как execution engine под BOSSMAN.

Поверх готового bcc/v2/opencode_bridge (клиент `opencode serve`). BOSSMAN
канонично держит миссии/задачи/бюджеты/права/историю; OpenCode — сессии кодинга.
health-check, привязка сессии к run/task (opencode_sessions), abort/fork/diff.

Без запущенного `opencode serve` health честно возвращает unavailable — это
не ошибка интеграции, а состояние окружения (бинарь ставится на машине).
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import utcnow
from ..v2.opencode_bridge import OpenCodeBridge
from ..v2.tables import opencode_sessions as oc_t
from . import Feature

router = APIRouter()


def _bridge(svc) -> OpenCodeBridge:
    return OpenCodeBridge(
        base_url=os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
        username=os.environ.get("OPENCODE_USER", "opencode"),
        password=os.environ.get("OPENCODE_PASSWORD"))


@router.get("/opencode/health")
async def health(request: Request):
    """Доступен ли opencode serve. Недоступен → honest unavailable (не 500)."""
    svc = request.app.state.svc
    bridge = _bridge(svc)
    import httpx
    try:
        async with bridge._client(5) as c:
            r = await c.get("/doc")
        return {"status": "online", "base_url": bridge.base_url,
                "http": r.status_code}
    except (httpx.HTTPError, OSError) as exc:
        return {"status": "unavailable", "base_url": bridge.base_url,
                "detail": f"{type(exc).__name__}",
                "hint": "запустите `opencode serve` на этой машине (см. docs/v2-pack/MCP_SKILLS_OPENCODE.md)"}


@router.post("/opencode/attach")
async def attach(request: Request):
    """Привязать OpenCode-сессию к BOSSMAN run/task (id хранится каноникой у нас)."""
    svc = request.app.state.svc
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(422, {"message": "нужен session_id"})
    async with svc.db.session() as s:
        oid = int((await s.execute(sa.insert(oc_t).values(
            session_id=session_id, task_id=body.get("task_id"), run_id=body.get("run_id"),
            project_path=body.get("project_path", ""), worktree_path=body.get("worktree_path", ""),
            status="active", created_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    return {"id": oid, "session_id": session_id}


@router.get("/opencode/sessions")
async def sessions(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(oc_t).order_by(oc_t.c.id.desc()).limit(100))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/opencode/sessions/{session_id}/abort")
async def abort(session_id: str, request: Request):
    svc = request.app.state.svc
    try:
        ok = await _bridge(svc).abort(session_id)
    except Exception as exc:
        raise HTTPException(502, {"message": f"OpenCode недоступен: {type(exc).__name__}"})
    return {"aborted": ok}


@router.get("/opencode/sessions/{session_id}/diff")
async def diff(session_id: str, request: Request):
    svc = request.app.state.svc
    try:
        return {"diff": await _bridge(svc).diff(session_id)}
    except Exception as exc:
        raise HTTPException(502, {"message": f"OpenCode недоступен: {type(exc).__name__}"})


FEATURE = Feature(name="opencode", router=router)
