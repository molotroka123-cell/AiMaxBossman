"""Feature 07 (часть) — Terminal с режимами sandbox/project_host/system_admin.

Поверх готовой bcc/v2/terminal_control (политика AUTO/ASK/DENY, sandbox=docker
по умолчанию). НЕТ глобального «весь компьютер»: allowed_roots ограничивают cwd.
ASK → approval; DENY → отказ. Kill/stdin/live-output. Запись сессий в БД.

В контейнере разработки docker может отсутствовать — тогда sandbox-запуск честно
падает ошибкой, а project_host (subprocess) работает в разрешённых корнях.
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import settings_kv, utcnow
from ..v2.tables import terminal_sessions as term_t
from ..v2.terminal_control import TerminalManager, TerminalPolicy
from . import Feature

ROOTS_KEY = "terminal.roots"          # список разрешённых корней для project_host
router = APIRouter()


def _mgr(svc) -> TerminalManager:
    if getattr(svc, "terminal", None) is None:
        svc.terminal = TerminalManager()
    return svc.terminal


async def _allowed_roots(svc) -> list[Path]:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == ROOTS_KEY))).first()
    if row and row[0]:
        try:
            return [Path(p) for p in json.loads(svc.vault.decrypt(row[0]))]
        except Exception:
            pass
    # по умолчанию — только каталог данных (sandbox монтирует cwd в контейнер)
    return [svc.settings.data_dir]


@router.get("/terminal/roots")
async def get_roots(request: Request):
    svc = request.app.state.svc
    return {"roots": [str(p) for p in await _allowed_roots(svc)]}


@router.post("/terminal/roots")
async def set_roots(request: Request):
    """Настройка разрешённых корней для project_host — осознанное расширение доступа."""
    svc = request.app.state.svc
    body = await request.json()
    roots = body.get("roots") or []
    enc = svc.vault.encrypt(json.dumps(roots))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == ROOTS_KEY))
        await s.execute(sa.insert(settings_kv).values(key=ROOTS_KEY, value_enc=enc))
        await s.commit()
    return {"roots": roots}


@router.post("/terminal/preview")
async def preview(request: Request):
    """Решение политики для команды БЕЗ запуска (AUTO/ASK/DENY + причина)."""
    svc = request.app.state.svc
    body = await request.json()
    mode = body.get("mode", "sandbox")
    cwd = Path(body.get("cwd") or svc.settings.data_dir)
    roots = await _allowed_roots(svc) if mode != "sandbox" else [cwd]
    policy = TerminalPolicy(allowed_roots=roots, mode=mode)
    decision = policy.decision(body.get("command", ""), cwd)
    return {"decision": decision, "mode": mode, "cwd": str(cwd)}


@router.post("/terminal/run")
async def run(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    mode = body.get("mode", "sandbox")
    cmd = body.get("command", "")
    cwd = Path(body.get("cwd") or svc.settings.data_dir)
    approved = bool(body.get("approved"))
    roots = await _allowed_roots(svc) if mode != "sandbox" else [cwd]
    policy = TerminalPolicy(allowed_roots=roots, mode=mode)
    decision = policy.decision(cmd, cwd)
    if decision == "deny":
        raise HTTPException(403, {"message": "команда запрещена политикой",
                                  "hint": "деструктивная команда или cwd вне разрешённых корней"})
    if decision == "ask" and not approved:
        # заводим approval и отвечаем, что нужно подтверждение
        appr = await svc.approvals.create(kind="terminal",
                                          preview=f"[{mode}] {cmd}\ncwd: {cwd}")
        raise HTTPException(202, {"message": "нужно подтверждение",
                                  "approval_id": appr.get("id"), "decision": "ask"})
    try:
        session = await _mgr(svc).start(cmd, cwd, policy, approved=True,
                                        network=bool(body.get("network")))
    except PermissionError as exc:
        raise HTTPException(403, {"message": str(exc)})
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(503, {"message": f"не удалось запустить: {exc}",
                                  "hint": "для sandbox нужен docker; попробуйте mode=project_host"})
    async with svc.db.session() as s:
        await s.execute(sa.insert(term_t).values(
            id=session.id, mode=mode, cwd=str(cwd), command=cmd, status="running",
            pid=session.proc.pid, started_at=utcnow()))
        await s.commit()
    await svc.bus.emit("agent.tool_call", tool="terminal", session_id=session.id,
                       command=cmd[:200], cwd=str(cwd))
    return {"session_id": session.id, "pid": session.proc.pid, "mode": mode}


@router.get("/terminal/sessions")
async def sessions(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(term_t).order_by(term_t.c.started_at.desc())
                                .limit(100))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/terminal/sessions/{session_id}")
async def session_status(session_id: str, request: Request):
    svc = request.app.state.svc
    mgr = _mgr(svc)
    if session_id not in mgr.sessions:
        raise HTTPException(404, {"message": "сессия не найдена (возможно, после рестарта)"})
    st = mgr.status(session_id)
    # синхронизируем БД по завершении
    if st["finished"]:
        async with svc.db.session() as s:
            await s.execute(sa.update(term_t).where(term_t.c.id == session_id).values(
                status="finished", exit_code=st["exit_code"], finished_at=utcnow()))
            await s.commit()
    return st


@router.post("/terminal/sessions/{session_id}/stdin")
async def stdin(session_id: str, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    try:
        await _mgr(svc).write_stdin(session_id, body.get("text", ""))
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(400, {"message": str(exc)})
    return {"ok": True}


@router.post("/terminal/sessions/{session_id}/kill")
async def kill(session_id: str, request: Request):
    svc = request.app.state.svc
    mgr = _mgr(svc)
    if session_id not in mgr.sessions:
        raise HTTPException(404, {"message": "сессия не найдена"})
    await mgr.kill(session_id)
    async with svc.db.session() as s:
        await s.execute(sa.update(term_t).where(term_t.c.id == session_id).values(
            status="killed", finished_at=utcnow()))
        await s.commit()
    await svc.bus.emit("agent.warning", tool="terminal", session_id=session_id, killed=True)
    return {"ok": True}


FEATURE = Feature(name="terminal", router=router)
