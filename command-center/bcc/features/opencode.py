"""Feature 07 (часть) — OpenCode как execution engine под BOSSMAN.

Поверх `bcc/v2/opencode_bridge` (клиент `opencode serve`). BOSSMAN канонично
держит миссии/задачи/бюджеты/права/историю; OpenCode — сессии кодинга. Здесь
операторский HTTP: health, старт сессии в одобренном каталоге, отправка задания,
статус, diff, abort, fork, привязка сессии к run/task (`opencode_sessions`).

Инструменты для МОДЕЛИ живут в `bcc/features/tools_opencode.py` — общий реестр,
общая очередь подтверждений. Здесь параллельной системы прав нет.

Без запущенного `opencode serve` health честно возвращает unavailable — это
не ошибка интеграции, а состояние окружения (бинарь ставится на машине).
"""
from __future__ import annotations

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..v2.opencode_bridge import assistant_text, diff_summary
from ..v2.tables import opencode_sessions as oc_t
from . import Feature
from .tools_opencode import (approved_dir, bridge_for, find_session, load_diff,
                             make_worktree, persist_diff, record_session, set_status)

router = APIRouter()


def _bridge(svc, directory: str = ""):
    return bridge_for(svc, directory)


async def _row_or_404(svc, session_id: str) -> dict:
    row = await find_session(svc, session_id)
    if not row:
        raise HTTPException(404, {"message": f"сессия {session_id} не привязана к BOSSMAN"})
    return row


def _directory(row: dict) -> str:
    return str(row.get("worktree_path") or row.get("project_path") or "")


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(502, {"message": f"OpenCode недоступен: {type(exc).__name__}",
                               "detail": str(exc)})


@router.get("/opencode/health")
async def health(request: Request):
    """Доступен ли opencode serve. Недоступен → honest unavailable (не 500)."""
    return await _bridge(request.app.state.svc).health(5)


@router.get("/opencode/roots")
async def roots(request: Request):
    from .tools_opencode import allowed_roots
    return {"roots": [str(p) for p in await allowed_roots(request.app.state.svc)]}


@router.post("/opencode/sessions")
async def start_session(request: Request):
    """Старт сессии в ОДОБРЕННОМ каталоге. Чужой путь — 403, а не подтверждение."""
    svc = request.app.state.svc
    body = await request.json()
    project, refusal = await approved_dir(svc, str(body.get("project_path") or ""))
    if project is None:
        raise HTTPException(403, {"message": refusal})

    worktree = project
    if body.get("worktree"):
        name = str(body.get("worktree_name") or f"task{body.get('task_id') or 0}")
        made, err = await make_worktree(project, name)
        if made is None:
            raise HTTPException(400, {"message": err})
        checked, refusal = await approved_dir(svc, str(made))
        if checked is None:
            raise HTTPException(403, {"message": refusal})
        worktree = checked

    try:
        session = await _bridge(svc).create_session(
            str(worktree), title=str(body.get("title") or ""),
            agent=str(body.get("agent") or ""))
    except (httpx.HTTPError, OSError) as exc:
        raise _unavailable(exc)
    session_id = str(session.get("id") or "")
    if not session_id:
        raise HTTPException(502, {"message": "OpenCode не вернул id сессии"})
    oid = await record_session(svc, session_id=session_id, task_id=body.get("task_id"),
                               run_id=body.get("run_id"), project_path=str(project),
                               worktree_path=str(worktree))
    return {"id": oid, "session_id": session_id, "directory": str(worktree),
            "project_path": str(project)}


@router.post("/opencode/sessions/{session_id}/send")
async def send(session_id: str, request: Request):
    """Задание в сессию. wait=false — не держим HTTP на длинном прогоне."""
    svc = request.app.state.svc
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, {"message": "нужен text"})
    row = await _row_or_404(svc, session_id)
    bridge = _bridge(svc)
    directory = _directory(row)
    try:
        if body.get("wait") is False:
            await bridge.prompt_async(session_id, text, directory)
            await set_status(svc, session_id, "running")
            return {"session_id": session_id, "queued": True}
        reply = await bridge.send_message(session_id, text, directory)
    except (httpx.HTTPError, OSError) as exc:
        raise _unavailable(exc)
    await set_status(svc, session_id, "active")
    return {"session_id": session_id, "text": assistant_text(reply),
            "message_id": str((reply.get("info") or {}).get("id") or "")}


@router.get("/opencode/sessions/{session_id}/status")
async def status(session_id: str, request: Request):
    svc = request.app.state.svc
    row = await _row_or_404(svc, session_id)
    try:
        state = await _bridge(svc).session_status(session_id, _directory(row))
    except (httpx.HTTPError, OSError) as exc:
        raise _unavailable(exc)
    return {"session_id": session_id, "state": state,
            "db_status": row.get("status"), "directory": _directory(row)}


@router.post("/opencode/attach")
async def attach(request: Request):
    """Привязать OpenCode-сессию к BOSSMAN run/task (id хранится каноникой у нас)."""
    svc = request.app.state.svc
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(422, {"message": "нужен session_id"})
    oid = await record_session(svc, session_id=str(session_id), task_id=body.get("task_id"),
                               run_id=body.get("run_id"),
                               project_path=body.get("project_path", ""),
                               worktree_path=body.get("worktree_path", ""))
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
    row = await find_session(svc, session_id)
    try:
        ok = await _bridge(svc).abort(session_id, _directory(row) if row else "")
    except Exception as exc:
        raise HTTPException(502, {"message": f"OpenCode недоступен: {type(exc).__name__}"})
    if row and ok:
        await set_status(svc, session_id, "aborted")
    return {"aborted": ok}


@router.post("/opencode/sessions/{session_id}/fork")
async def fork(session_id: str, request: Request):
    svc = request.app.state.svc
    body = await request.json() if await request.body() else {}
    row = await _row_or_404(svc, session_id)
    try:
        child = await _bridge(svc).fork(session_id, body.get("message_id"), _directory(row))
    except (httpx.HTTPError, OSError) as exc:
        raise _unavailable(exc)
    child_id = str(child.get("id") or "")
    if child_id:
        await record_session(svc, session_id=child_id, task_id=row.get("task_id"),
                             run_id=row.get("run_id"),
                             project_path=str(row.get("project_path") or ""),
                             worktree_path=str(row.get("worktree_path") or ""))
    return {"session_id": child_id, "parent_id": session_id}


@router.get("/opencode/sessions/{session_id}/diff")
async def diff(session_id: str, request: Request):
    """Живой дифф из OpenCode; если сервер недоступен — сохранённый снимок."""
    svc = request.app.state.svc
    row = await find_session(svc, session_id)
    try:
        diffs = await _bridge(svc).diff(session_id, None, _directory(row) if row else "")
    except (httpx.HTTPError, OSError) as exc:
        if row and row.get("run_id") is not None:
            saved = await load_diff(svc, row["run_id"], session_id)
            if saved:
                return {"diff": saved, "summary": diff_summary(saved), "source": "snapshot",
                        "detail": f"OpenCode недоступен: {type(exc).__name__}"}
        raise _unavailable(exc)
    if row:
        await persist_diff(svc, row.get("run_id"), session_id, diffs)
    return {"diff": diffs, "summary": diff_summary(diffs), "source": "live"}


@router.get("/opencode/sessions/{session_id}/children")
async def children(session_id: str, request: Request):
    svc = request.app.state.svc
    row = await find_session(svc, session_id)
    try:
        return {"children": await _bridge(svc).children(
            session_id, _directory(row) if row else "")}
    except (httpx.HTTPError, OSError) as exc:
        raise _unavailable(exc)


@router.get("/opencode/sessions/{session_id}/todo")
async def todo(session_id: str, request: Request):
    svc = request.app.state.svc
    row = await find_session(svc, session_id)
    try:
        return {"todo": await _bridge(svc).todo(session_id, _directory(row) if row else "")}
    except (httpx.HTTPError, OSError) as exc:
        raise _unavailable(exc)


FEATURE = Feature(name="opencode", router=router)
