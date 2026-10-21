"""Feature 07 (часть) — Terminal с режимами sandbox/project_host/system_admin.

Поверх готовой bcc/v2/terminal_control (политика AUTO/ASK/DENY, sandbox=docker
по умолчанию). НЕТ глобального «весь компьютер»: allowed_roots ограничивают cwd.
ASK → approval; DENY → отказ. Kill/stdin/live-output. Запись сессий в БД.

В контейнере разработки docker может отсутствовать — тогда sandbox-запуск честно
падает ошибкой, а project_host (subprocess) работает в разрешённых корнях.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from ..api import COOKIE_NAME, _same_origin
from ..db import settings_kv, tasks as tasks_t, utcnow
from ..v2.tables import terminal_sessions as term_t
from ..v2.terminal_control import TerminalManager, TerminalPolicy, within
from . import Feature

ROOTS_KEY = "terminal.roots"          # список разрешённых корней для project_host
router = APIRouter()
ws_router = APIRouter()


def _mgr(svc) -> TerminalManager:
    if getattr(svc, "terminal", None) is None:
        svc.terminal = TerminalManager(data_dir=Path(svc.settings.data_dir))
    mgr = svc.terminal
    if getattr(mgr, "data_dir", None) is None:
        mgr.data_dir = Path(svc.settings.data_dir)
    # Журнал вывода и запись итога в БД настраиваются здесь, а не в api.py: менеджер
    # создаётся там без аргументов, а нужный ему каталог данных знает только фича.
    if getattr(mgr, "log_dir", None) is None:
        mgr.log_dir = Path(svc.settings.data_dir) / "terminal" / "logs"
    if getattr(mgr, "on_finish", None) is None:
        async def finished(session, _svc=svc) -> None:
            await _sync_finished(_svc, session)
        mgr.on_finish = finished
    return mgr


async def _sync_finished(svc, session) -> None:
    """Процесс закончился — строка получает итог сразу, а не когда кто-нибудь
    спросит статус. Иначе после рестарта честно завершённая команда выглядела бы
    потерянной. Убитая (`killed`) и потерянная (`lost`) строки не перезаписываются."""
    async with svc.db.session() as s:
        await s.execute(sa.update(term_t).where(
            term_t.c.id == session.id, term_t.c.status == "running").values(
            status="finished", exit_code=session.exit_code, finished_at=utcnow()))
        await s.commit()


# ------------------------------------------------ Docker: честная проба для UI
#
# Режим по умолчанию на странице «Терминал» был sandbox всегда, а на ПК владельца
# без Docker каждый запуск падал 503. Теперь UI спрашивает, есть ли Docker на самом
# деле: CLI найден И демон отвечает. Результат кэшируется — проба запускает процесс.

_DOCKER_TTL_S = 30.0
_docker_cache: tuple[float, dict] | None = None


async def probe_docker(*, force: bool = False) -> dict:
    global _docker_cache
    now = time.monotonic()
    if not force and _docker_cache is not None and now - _docker_cache[0] < _DOCKER_TTL_S:
        return _docker_cache[1]
    exe = shutil.which("docker")
    if not exe:
        result = {"available": False, "detail": "docker не найден в PATH"}
    else:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            ok = proc.returncode == 0 and bool(out.strip())
            result = {"available": ok,
                      "detail": (f"Docker {out.decode(errors='replace').strip()}" if ok
                                 else "docker найден, но демон не отвечает")}
        except (OSError, asyncio.TimeoutError) as exc:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            result = {"available": False, "detail": f"проба docker не удалась: {type(exc).__name__}"}
    _docker_cache = (now, result)
    return result


@router.get("/terminal/capabilities")
async def capabilities(request: Request):
    """Что реально доступно на этой машине — UI выбирает режим по умолчанию отсюда:
    sandbox, только если Docker отвечает, иначе project_host (он всегда спрашивает)."""
    docker = await probe_docker()
    return {"docker": docker, "default_mode": "sandbox" if docker["available"] else "project_host",
            "modes": ["sandbox", "project_host", "system_admin"]}


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
        approval_id = body.get("approval_id")
        if not approval_id and body.get("bypass_permissions") is True:
            appr_auto = await svc.approvals.create(kind="terminal", preview=preview)
            await svc.approvals.decide(int(appr_auto["id"]), True, "owner_auto_bypass")
            approval_id = appr_auto["id"]
        if not await svc.approvals.consume(approval_id, kind="terminal", preview=preview):
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
    if session.finished:
        # Быстрая команда закончилась ДО того, как строка появилась: итог из
        # `on_finish` тогда никуда не попал.
        await _sync_finished(svc, session)
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


async def _stored_status(svc, mgr: TerminalManager, session_id: str) -> dict | None:
    """Статус сессии, которой нет в памяти: строка БД + сохранённый хвост вывода."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(term_t).where(term_t.c.id == session_id))).first()
    tail = mgr.read_log(session_id)
    if row is None and tail is None:
        return None
    m = dict(row._mapping) if row is not None else {}
    return {"id": session_id, "cwd": m.get("cwd", ""), "cmd": m.get("command", ""),
            "mode": m.get("mode", ""), "pid": m.get("pid"),
            # `running` без живого процесса в памяти — уже не «идёт»: это потерянная сессия
            "finished": True, "exit_code": m.get("exit_code"),
            "status": "lost" if m.get("status") == "running" else m.get("status", "finished"),
            "output_tail": (tail or [])[-200:], "from_log": tail is not None}


@router.get("/terminal/sessions/{session_id}/log")
async def session_log(session_id: str, request: Request):
    """Итог и хвост вывода сессии, которой уже нет в памяти (вытеснена или рестарт)."""
    svc = request.app.state.svc
    mgr = _mgr(svc)
    if session_id in mgr.sessions:
        st = mgr.status(session_id)
        return {**st, "status": "finished" if st["finished"] else "running", "from_log": False}
    stored = await _stored_status(svc, mgr, session_id)
    if stored is None:
        raise HTTPException(404, {"message": "сессия не найдена: ни записи в истории, ни сохранённого вывода"})
    return stored


@router.get("/terminal/sessions/{session_id}")
async def session_status(session_id: str, request: Request):
    svc = request.app.state.svc
    mgr = _mgr(svc)
    if session_id not in mgr.sessions:
        # 404 по-прежнему значит «нет В ПАМЯТИ». Итог и вывод такой сессии отдаёт
        # `/sessions/{id}/log` — из БД и сохранённого хвоста.
        raise HTTPException(404, {"message": "сессия не найдена (возможно, после рестарта)",
                                  "hint": f"сохранённый вывод: /api/terminal/sessions/{session_id}/log"})
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


@router.post("/terminal/sessions/{session_id}/sigint")
async def sigint(session_id: str, request: Request):
    """Отправить Ctrl+C (SIGINT) живой сессии терминала."""
    svc = request.app.state.svc
    mgr = _mgr(svc)
    if session_id not in mgr.sessions:
        raise HTTPException(404, {"message": "сессия не найдена"})
    res = await mgr.sigint(session_id)
    if res.get("finished"):
        async with svc.db.session() as s:
            await s.execute(sa.update(term_t).where(
                term_t.c.id == session_id, term_t.c.status == "running").values(
                status="killed", exit_code=res.get("exit_code"), finished_at=utcnow()))
            await s.commit()
    await svc.bus.emit("agent.warning", tool="terminal", session_id=session_id, sigint=True)
    return res


@router.get("/terminal/files")
async def terminal_files(request: Request, path: str | None = Query(default=None)):
    """Просмотр списка файлов или содержимого текстового файла внутри разрешённых корней."""
    svc = request.app.state.svc
    roots = await _allowed_roots(svc)
    target = Path(path or (roots[0] if roots else svc.settings.data_dir)).expanduser().resolve()
    if not within(target, roots):
        raise HTTPException(403, {"message": "путь вне разрешённых корней", "path": str(target)})
    if not target.exists():
        raise HTTPException(404, {"message": "путь не найден", "path": str(target)})
    if target.is_dir():
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": st.st_size if child.is_file() else None,
                })
            except OSError:
                continue
        return {"kind": "dir", "path": str(target), "entries": entries}
    try:
        raw = target.read_bytes()[:65536]
        text = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(400, {"message": f"не удалось прочитать файл: {exc}"})
    return {"kind": "file", "path": str(target), "name": target.name,
            "content": text, "truncated": target.stat().st_size > 65536}


@router.get("/terminal/diff")
async def terminal_diff(request: Request, cwd: str | None = Query(default=None)):
    """Быстрый просмотр git status + git diff внутри разрешённого корня."""
    svc = request.app.state.svc
    roots = await _allowed_roots(svc)
    target = Path(cwd or (roots[0] if roots else svc.settings.data_dir)).expanduser().resolve()
    if not within(target, roots):
        raise HTTPException(403, {"message": "cwd вне разрешённых корней", "cwd": str(target)})
    if not target.is_dir():
        raise HTTPException(404, {"message": "папка не найдена", "cwd": str(target)})
    git_exe = shutil.which("git")
    if not git_exe:
        return {"ok": False, "cwd": str(target), "status": "", "diff": "", "detail": "git не найден"}
    try:
        p_st = await asyncio.create_subprocess_exec(
            git_exe, "status", "--short", "--branch", cwd=str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out_st, _ = await asyncio.wait_for(p_st.communicate(), timeout=8.0)
        p_df = await asyncio.create_subprocess_exec(
            git_exe, "diff", "--stat", cwd=str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out_df, _ = await asyncio.wait_for(p_df.communicate(), timeout=8.0)
        return {
            "ok": p_st.returncode == 0,
            "cwd": str(target),
            "status": out_st.decode("utf-8", errors="replace")[:16000],
            "diff": out_df.decode("utf-8", errors="replace")[:32000],
        }
    except (OSError, asyncio.TimeoutError) as exc:
        return {"ok": False, "cwd": str(target), "status": "", "diff": "", "detail": str(exc)}


@ws_router.websocket("/terminal/sessions/{session_id}/stream")
async def terminal_stream_ws(ws: WebSocket, session_id: str,
                             after: int = Query(default=0, ge=0),
                             token: str | None = Query(default=None)):
    """Интерактивный WebSocket-поток сессии терминала с replay после реконнекта,
    отправкой stdin, Ctrl+C (sigint) и полной остановкой (kill)."""
    svc = ws.app.state.svc
    origin = ws.headers.get("origin")
    if origin is not None and not _same_origin(origin, ws.headers.get("host", "")):
        await ws.close(code=4403)
        return
    sess_cookie = await svc.sessions.get(ws.cookies.get(COOKIE_NAME))
    bearer = (ws.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    effective_tok = token or bearer or None
    if sess_cookie is None and not svc.auth.check(effective_tok):
        await ws.close(code=4401)
        return
    mgr = _mgr(svc)
    session = mgr.sessions.get(session_id)
    if session is None:
        stored = await _stored_status(svc, mgr, session_id)
        if stored is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        lines = stored.get("output_tail") or []
        for idx, line in enumerate(lines, start=1):
            if idx > after:
                await ws.send_json({"type": "terminal.output", "session_id": session_id,
                                    "seq": idx, "line": line, "replayed": True})
        await ws.send_json({"type": "terminal.exit", "session_id": session_id,
                            "seq": len(lines) + 1, "exit_code": stored.get("exit_code"),
                            "finished": True, "status": stored.get("status", "finished")})
        await ws.close()
        return

    await ws.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1024)
    session.subscribers.add(queue)
    try:
        # 1. Replay buffered lines after `after` cursor
        snapshot_lines = list(session.output)
        base_seq = max(0, session.seq - len(snapshot_lines))
        await ws.send_json({
            "type": "terminal.hello",
            "session_id": session.id,
            "cwd": str(session.cwd),
            "cmd": session.cmd,
            "mode": session.mode,
            "pid": session.proc.pid,
            "finished": session.finished,
            "exit_code": session.exit_code,
            "seq": session.seq,
        })
        for offset, line in enumerate(snapshot_lines, start=1):
            line_seq = base_seq + offset
            if line_seq > after:
                await ws.send_json({
                    "type": "terminal.output",
                    "session_id": session.id,
                    "seq": line_seq,
                    "line": line,
                    "replayed": True,
                })
        if session.finished:
            await ws.send_json({
                "type": "terminal.exit",
                "session_id": session.id,
                "seq": session.seq,
                "exit_code": session.exit_code,
                "finished": True,
            })

        async def _pump_out() -> None:
            while True:
                evt = await queue.get()
                await ws.send_json(evt)
                if evt.get("type") == "terminal.exit":
                    break

        async def _pump_in() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw) if raw.strip().startswith("{") else {"action": "stdin", "text": raw}
                except ValueError:
                    msg = {"action": "stdin", "text": raw}
                action = msg.get("action") or msg.get("type") or "stdin"
                if action == "stdin":
                    text_in = str(msg.get("text") or msg.get("data") or "")
                    if text_in:
                        with contextlib.suppress(Exception):
                            await mgr.write_stdin(session.id, text_in)
                        await ws.send_json({"type": "terminal.stdin.ack", "session_id": session.id})
                elif action in ("sigint", "ctrl_c", "interrupt"):
                    res = await mgr.sigint(session.id)
                    await ws.send_json({"type": "terminal.sigint", "session_id": session.id, **res})
                elif action in ("kill", "stop"):
                    await mgr.kill(session.id)
                    await ws.send_json({"type": "terminal.killed", "session_id": session.id,
                                        "exit_code": session.exit_code})
                    break
                elif action == "ping":
                    await ws.send_json({"type": "pong", "session_id": session.id, "seq": session.seq})

        out_task = asyncio.create_task(_pump_out())
        in_task = asyncio.create_task(_pump_in())
        done, pending = await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await p
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        session.subscribers.discard(queue)


# ------------------------------------------------- жизненный цикл сессий

async def _mark_killed(svc, ids: list[str]) -> None:
    if not ids:
        return
    async with svc.db.session() as s:
        await s.execute(sa.update(term_t).where(term_t.c.id.in_(ids)).values(
            status="killed", finished_at=utcnow()))
        await s.commit()


async def reconcile_sessions(svc) -> int:
    """Старт процесса: строки `running` без живой сессии в памяти → `lost`.

    Запись в БД переживала рестарт, процесс под ней и сессия в памяти — нет, и
    строка оставалась «running» навсегда. `lost`, а не `finished`: код возврата и
    исход команды неизвестны. Процессы прежнего запуска здесь НЕ убиваются: по
    одному pid после рестарта нельзя доказать, что он наш, а не чужой с тем же номером."""
    live = set(_mgr(svc).sessions)
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(term_t.c.id).where(term_t.c.status == "running"))).fetchall()
    lost = [str(r[0]) for r in rows if str(r[0]) not in live]
    if lost:
        async with svc.db.session() as s:
            await s.execute(sa.update(term_t).where(term_t.c.id.in_(lost)).values(
                status="lost", finished_at=utcnow()))
            await s.commit()
    return len(lost)


async def shutdown_sessions(svc) -> list[str]:
    """Остановка сервисов: живые сессии не должны пережить backend. Процессы,
    запущенные владельцем через терминал, иначе оставались бы без присмотра, а
    глобальный STOP их уже не видел (он смотрит только на живые ручки)."""
    mgr = getattr(svc, "terminal", None)
    if mgr is None:
        return []
    ids = await mgr.kill_all()
    with contextlib.suppress(Exception):
        await _mark_killed(svc, ids)
    return ids


async def stop_task_sessions(svc, task_id: int) -> list[str]:
    """Per-task STOP: команды этой задачи останавливаются вместе с ней.

    `terminal.run` возвращает управление по таймауту, а команда продолжает идти;
    раньше остановка задачи её не трогала — убить можно было только глобальным STOP."""
    mgr = getattr(svc, "terminal", None)
    if mgr is None:
        return []
    ids = await mgr.kill_owned(str(task_id))
    if ids:
        with contextlib.suppress(Exception):
            await _mark_killed(svc, ids)
        with contextlib.suppress(Exception):
            await svc.bus.emit("agent.warning", tool="terminal", task_id=task_id,
                               killed=len(ids), reason="task.stopped")
    return ids


async def sweep_stopped_tasks(svc) -> list[str]:
    """Страховка к подписке на шину: живые сессии остановленных задач добиваются
    по состоянию БД (подписчик шины может потерять событие при переполнении)."""
    mgr = getattr(svc, "terminal", None)
    owners = {s.owner for s in (mgr.sessions.values() if mgr else ())
              if not s.finished and s.owner and str(s.owner).isdigit()}
    if not owners:
        return []
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(tasks_t.c.id).where(
            tasks_t.c.id.in_([int(o) for o in owners]),
            tasks_t.c.status.in_(("stopped", "cancelled"))))).fetchall()
    killed: list[str] = []
    for r in rows:
        killed += await stop_task_sessions(svc, int(r[0]))
    return killed


async def _on_task_stopped(svc, msg: dict) -> None:
    with contextlib.suppress(Exception):
        if msg.get("task_id") is not None:
            await stop_task_sessions(svc, int(msg["task_id"]))


async def _guardian(svc) -> None:
    """Живёт, пока живы сервисы; отмена при остановке = хук завершения фичи
    (у Feature нет shutdown, а api.py фичам не принадлежит)."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await shutdown_sessions(svc)
        raise


async def _sweep_tick(svc) -> None:
    await sweep_stopped_tasks(svc)


async def _setup(svc) -> None:
    _mgr(svc)                                     # log_dir и запись итога в БД
    with contextlib.suppress(Exception):
        await reconcile_sessions(svc)
    if hasattr(svc, "_tasks"):
        from .tools_browser import watch_bus
        svc._tasks.append(asyncio.create_task(
            watch_bus(svc, frozenset({"task.stopped"}), lambda msg: _on_task_stopped(svc, msg)),
            name="bcc-terminal-task-stop"))
        svc._tasks.append(asyncio.create_task(_guardian(svc), name="bcc-terminal-guardian"))


FEATURE = Feature(name="terminal", router=router, ws_router=ws_router, setup=_setup,
                  tick=_sweep_tick, tick_seconds=30.0)
