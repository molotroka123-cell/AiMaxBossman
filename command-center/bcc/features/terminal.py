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


async def _retire_finished(svc, mgr: TerminalManager) -> None:
    """Освободить память от давно завершённых сессий, НЕ потеряв историю.

    Долговечная история команд владельца живёт в БД и отдаётся
    `/api/terminal/sessions`; в памяти у сессии остаётся только живой процесс,
    его транспорт и хвост вывода. Этот словарь не чистился никогда — длинный
    владельческий прогон намерил ~5.5 КБ на команду, которые не возвращаются.

    Статус выселенной сессии дописывается в базу здесь же. Иначе строка
    осталась бы «running» навсегда: раньше её закрывал ТОЛЬКО GET статуса,
    которого могло и не случиться. Запрос по забытому id отвечает честным 404
    «сессия не найдена (возможно, после рестарта)» — тем же, что и после
    перезапуска продукта.
    """
    # Не теряем единственную копию финального статуса при ошибке БД:
    # неудачный commit оставляет кандидатов доступными для повторной записи.
    retired = mgr.retire_finished(remove=False)
    if not retired:
        return
    async with svc.db.session() as s:
        for st in retired:
            await s.execute(sa.update(term_t).where(
                term_t.c.id == st["id"], term_t.c.status == "running").values(
                status="finished", exit_code=st["exit_code"], finished_at=utcnow()))
        await s.commit()
    for st in retired:
        mgr.sessions.pop(st["id"], None)


def _bad_roots(message: str) -> HTTPException:
    return HTTPException(400, {"message": message,
                               "hint": "roots — список абсолютных путей к существующим папкам проекта"})


def _validate_roots(raw, *, has_body: bool = True) -> list[str]:
    """C3: корни — список абсолютных путей к существующим папкам, не корень диска.

    Ошибка в любом элементе отклоняет весь список (400), сохранённые корни не
    меняются. Пустой список/отсутствие поля — сброс к каталогу данных.
    """
    if not has_body:
        raise _bad_roots("тело запроса должно быть JSON-объектом с полем roots")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad_roots("roots должен быть списком путей, а не строкой или другим значением")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise _bad_roots(f"каждый корень должен быть непустой строкой-путём: {item!r}")
        p = Path(item.strip()).expanduser()
        if not p.is_absolute():
            raise _bad_roots(f"путь должен быть абсолютным: {item}")
        try:
            resolved = p.resolve()
        except (OSError, RuntimeError):
            raise _bad_roots(f"не удалось разобрать путь: {item}")
        if resolved.parent == resolved:
            raise _bad_roots(f"корень диска/файловой системы запрещён как разрешённая папка: "
                             f"{resolved} — укажите конкретную папку проекта")
        if not resolved.is_dir():
            raise _bad_roots(f"папка не существует или это не папка: {item}")
        if str(resolved) not in out:
            out.append(str(resolved))
    return out


@router.get("/terminal/roots")
async def get_roots(request: Request):
    svc = request.app.state.svc
    return {"roots": [str(p) for p in await _allowed_roots(svc)]}


@router.post("/terminal/roots")
async def set_roots(request: Request):
    """Настройка разрешённых корней для project_host — осознанное расширение доступа."""
    svc = request.app.state.svc
    body = await request.json()
    roots = _validate_roots(body.get("roots") if isinstance(body, dict) else None,
                            has_body=isinstance(body, dict))
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
    cwd = Path(body.get("cwd") or svc.settings.data_dir).expanduser().resolve()
    # F-009: корни одни для всех режимов (sandbox больше не «[cwd]»)
    policy = TerminalPolicy(allowed_roots=await _allowed_roots(svc), mode=mode)
    decision = policy.decision(body.get("command", ""), cwd)
    return {"decision": decision, "mode": mode, "cwd": str(cwd)}


@router.post("/terminal/run")
async def run(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    mode = body.get("mode", "sandbox")
    cmd = body.get("command", "")
    cwd = Path(body.get("cwd") or svc.settings.data_dir).expanduser().resolve()
    # F-009: sandbox больше не объявляет cwd корнем — корни владельца для всех режимов.
    policy = TerminalPolicy(allowed_roots=await _allowed_roots(svc), mode=mode)
    decision = policy.decision(cmd, cwd)
    if decision == "deny":
        raise HTTPException(403, {"message": "команда запрещена политикой",
                                  "hint": "деструктивная команда или cwd вне разрешённых корней"})
    preview = f"[{mode}] {cmd}\ncwd: {cwd}"
    if decision == "ask":
        # F-015: «approved: true» в теле запроса — не подтверждение. Подтверждение —
        # это запись approvals(kind=terminal, status=approved) с ТЕМ ЖЕ preview
        # (команда+cwd+режим), предъявленная как approval_id и потребляемая один раз.
        if body.get("approved") and not body.get("approval_id"):
            raise HTTPException(403, {"message": "самоутверждённый флаг approved не принимается: "
                                                 "нужен approval_id одобренной записи"})
        if not await svc.approvals.consume(body.get("approval_id"), kind="terminal",
                                           preview=preview):
            appr = await svc.approvals.create(kind="terminal", preview=preview)
            raise HTTPException(202, {"message": "нужно подтверждение",
                                      "approval_id": appr.get("id"), "decision": "ask"})
    mgr = _mgr(svc)
    await _retire_finished(svc, mgr)
    try:
        session = await mgr.start(cmd, cwd, policy, approved=True,
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
            await s.execute(sa.update(term_t).where(
                term_t.c.id == session_id, term_t.c.status == "running").values(
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
    state = mgr.status(session_id)
    if not state["finished"] or mgr.sessions[session_id].proc.returncode is None:
        raise HTTPException(503, {"message": "остановка процесса не подтверждена"})
    async with svc.db.session() as s:
        await s.execute(sa.update(term_t).where(term_t.c.id == session_id).values(
            status="killed", exit_code=state["exit_code"], finished_at=utcnow()))
        await s.commit()
    await svc.bus.emit("agent.warning", tool="terminal", session_id=session_id, killed=True)
    return {"ok": True}


FEATURE = Feature(name="terminal", router=router)
