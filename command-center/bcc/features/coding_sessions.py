"""Coding Sessions — HTTP-доступ к реальному CodingWorktreeManager.

Операторская цепочка Session → Diff → Merge не должна быть мёртвым концом:
бэкенд (bcc.coding_session) уже реализован и остаётся ЕДИНСТВЕННЫМ движком
diff/merge — здесь только API-обвязка, второй движок не создаётся.

* source_repo разрешён только внутри канонических allowed_roots (тот же
  периметр, что у code-intel/terminal/opencode);
* worktree живёт в data_dir/coding-sessions (конфайнмент внутри менеджера);
* merge-политика: конфликты → 409, слияния с конфликтами не существует
  (allow_conflicts из менеджера наружу не выставляется);
* ошибки менеджера мапятся честно: 404 unknown / 409 already active / 400.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..coding_session import CodingSessionError, CodingWorktreeManager
from . import Feature
from .tools_code import _within, allowed_roots

router = APIRouter()


class SessionCreate(BaseModel):
    session_id: str
    source_repo: str
    base_ref: str = "HEAD"


class MergeIn(BaseModel):
    into: str | None = None


def _mgr(svc) -> CodingWorktreeManager:
    if getattr(svc, "coding_sessions", None) is None:
        svc.coding_sessions = CodingWorktreeManager(svc.settings.data_dir / "coding-sessions")
    return svc.coding_sessions


async def _confined_repo(svc, raw: str) -> str:
    if not str(raw or "").strip():
        raise HTTPException(400, {"message": "source_repo обязателен"})
    roots = await allowed_roots(svc)
    try:
        p = Path(str(raw)).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        raise HTTPException(400, {"message": f"путь репозитория недоступен: {raw}"})
    if not any(_within(p, [r]) for r in roots):
        raise HTTPException(403, {"message": "репозиторий вне разрешённых корней",
                                  "hint": "добавьте корень в настройки code/terminal roots"})
    return str(p)


def _err(exc: CodingSessionError) -> HTTPException:
    text = str(exc)
    if text.startswith("unknown session"):
        return HTTPException(404, {"message": text})
    if "already active" in text:
        return HTTPException(409, {"message": text})
    return HTTPException(400, {"message": text})


@router.get("/coding-sessions")
async def list_sessions(request: Request):
    mgr = _mgr(request.app.state.svc)
    return mgr.list_sessions()


@router.post("/coding-sessions")
async def create_session(body: SessionCreate, request: Request):
    svc = request.app.state.svc
    repo = await _confined_repo(svc, body.source_repo)
    try:
        meta = await _mgr(svc).create(body.session_id, repo, base_ref=body.base_ref)
    except CodingSessionError as exc:
        raise _err(exc)
    await svc.bus.emit("coding.session.created", session_id=meta.session_id,
                       source_repo=meta.source_repo, base_ref=meta.base_ref)
    return meta.__dict__


@router.get("/coding-sessions/{session_id}")
async def session_status(session_id: str, request: Request):
    try:
        return await _mgr(request.app.state.svc).status(session_id)
    except CodingSessionError as exc:
        raise _err(exc)


@router.get("/coding-sessions/{session_id}/diff")
async def session_diff(session_id: str, request: Request):
    try:
        return await _mgr(request.app.state.svc).diff(session_id)
    except CodingSessionError as exc:
        raise _err(exc)


@router.post("/coding-sessions/{session_id}/merge_preview")
async def merge_preview(session_id: str, body: MergeIn, request: Request):
    try:
        return await _mgr(request.app.state.svc).merge_preview(session_id, into=body.into)
    except CodingSessionError as exc:
        raise _err(exc)


@router.post("/coding-sessions/{session_id}/merge")
async def merge(session_id: str, body: MergeIn, request: Request):
    """Политика: чистый merge или отказ. Конфликты → 409 (никогда не «влитой мусор»)."""
    svc = request.app.state.svc
    try:
        # into=None → менеджер сам резолвит цель (текущая ветка источника → база)
        res = await _mgr(svc).merge(session_id, into=body.into)
    except CodingSessionError as exc:
        raise _err(exc)
    if not res.get("merged"):
        raise HTTPException(409, {"message": f"merge отклонён: {res.get('reason')}",
                                  "conflicts": res.get("conflicts", []),
                                  "detail": res.get("detail", "")})
    await svc.bus.emit("coding.session.merged", session_id=session_id,
                       into=res.get("into", ""), head=res.get("head", ""))
    return res


@router.post("/coding-sessions/{session_id}/discard")
async def discard(session_id: str, request: Request):
    svc = request.app.state.svc
    try:
        res = await _mgr(svc).discard(session_id)
    except CodingSessionError as exc:
        raise _err(exc)
    await svc.bus.emit("coding.session.discarded", session_id=session_id)
    return res


FEATURE = Feature(name="coding_sessions", router=router)
