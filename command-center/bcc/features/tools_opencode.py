"""V2.1 фаза F — OpenCode как инструмент агента (канонический реестр).

HTTP-страница OpenCode уже была (`bcc/features/opencode.py`); здесь тот же
рантайм подключается к `bcc.tools.REGISTRY`, чтобы МОДЕЛЬ могла запускать
кодинг-сессию через общий tool-loop с правами AUTO/ASK/DENY. Своей очереди
подтверждений тут нет и быть не может — только канонический слой.

Границы доступа (жёстко):
  * OpenCode получает РОВНО ОДИН одобренный путь проекта/worktree
    (query-параметр `directory`), а не весь компьютер;
  * путь проверяется по корням `opencode.roots` (или `terminal.roots`,
    или каталог данных) — выход за них отказ, а не подтверждение;
  * для `send`/`status`/`diff`/`abort` каталог НЕ берётся из аргументов модели:
    он читается из строки `opencode_sessions`, заведённой при старте.

Эффекты:
  * `opencode.session.start`, `opencode.send` — ASK: это запуск автономного
    кодинг-агента, человек подтверждает даже при выданном праве (хук политики
    ужесточает решение, ослабить его инструмент не может);
  * `opencode.status`, `opencode.diff`, `opencode.abort` — AUTO: чтение
    состояния и остановка работы безопасны.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import sqlalchemy as sa

from ..db import run_events, settings_kv, utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2.opencode_bridge import (OpenCodeBridge, assistant_text, diff_summary,
                                  render_diff)
from ..v2.tables import opencode_sessions as oc_t
from . import Feature

ROOTS_KEY = "opencode.roots"
TERMINAL_ROOTS_KEY = "terminal.roots"      # общий список корней, если свой не задан
DIFF_LIMIT = 8000
DEFAULT_URL = "http://127.0.0.1:4096"


# ------------------------------------------------------------------ конфиг

def bridge_for(svc, directory: str = "") -> OpenCodeBridge:
    """Клиент по настройкам окружения. Один и тот же для HTTP и для инструментов."""
    return OpenCodeBridge(
        base_url=os.environ.get("OPENCODE_URL", DEFAULT_URL),
        username=os.environ.get("OPENCODE_USER", "opencode"),
        password=os.environ.get("OPENCODE_PASSWORD"),
        directory=directory)


async def allowed_roots(svc) -> list[Path]:
    """Одобренные корни. Свой ключ → общий терминальный → каталог данных."""
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(settings_kv.c.key, settings_kv.c.value_enc)
            .where(settings_kv.c.key.in_([ROOTS_KEY, TERMINAL_ROOTS_KEY])))).fetchall()
    found = {r[0]: r[1] for r in rows}
    for key in (ROOTS_KEY, TERMINAL_ROOTS_KEY):
        raw = found.get(key)
        if not raw:
            continue
        try:
            values = json.loads(svc.vault.decrypt(raw))
        except Exception:
            continue
        if isinstance(values, list) and values:
            return [Path(str(p)).expanduser() for p in values]
    return [Path(svc.settings.data_dir)]


def _within(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


async def approved_dir(svc, raw: str) -> tuple[Path | None, str]:
    """Путь → (одобренный путь, причина отказа). Пустая причина = можно."""
    if not raw:
        return None, "не указан project_path, а рабочего каталога у задачи нет"
    path = Path(str(raw)).expanduser()
    roots = await allowed_roots(svc)
    if not _within(path, roots):
        return None, (f"путь {path} вне одобренных корней "
                      f"({', '.join(str(r) for r in roots)}); "
                      f"OpenCode не получает доступ ко всему компьютеру")
    if not path.exists():
        return None, f"каталог {path} не существует"
    if not path.is_dir():
        return None, f"{path} не каталог"
    return path, ""


# --------------------------------------------------------------- worktree

async def _git(cwd: Path, *args: str, timeout: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "git не уложился в таймаут"
    return int(proc.returncode or 0), (out or b"").decode("utf-8", "replace")


async def make_worktree(project: Path, name: str) -> tuple[Path | None, str]:
    """Отдельный git worktree под задачу: правки агента не трогают основную ветку.

    Если проект не git-репозиторий — честная ошибка, а не тихий откат на сам
    проект: иначе автономный агент писал бы прямо в рабочее дерево человека.
    """
    code, out = await _git(project, "rev-parse", "--show-toplevel")
    if code != 0:
        return None, f"{project} не git-репозиторий, worktree не создать: {out.strip()}"
    top = Path(out.strip() or str(project))
    target = top.parent / f"{top.name}-{name}"
    if target.exists():
        return target, ""
    code, out = await _git(top, "worktree", "add", "-b", f"bossman/{name}", str(target))
    if code != 0:
        return None, f"git worktree add не удался: {out.strip()}"
    return target, ""


# --------------------------------------------------------------- хранилище

async def record_session(svc, *, session_id: str, task_id, run_id,
                         project_path: str, worktree_path: str,
                         status: str = "active") -> int:
    """Маппинг BOSSMAN ↔ OpenCode. Переживает рестарт процесса."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(oc_t.c.id)
                               .where(oc_t.c.session_id == session_id))).first()
        if row:
            await s.execute(sa.update(oc_t).where(oc_t.c.id == row[0]).values(
                task_id=task_id, run_id=run_id, project_path=project_path,
                worktree_path=worktree_path, status=status, updated_at=utcnow()))
            await s.commit()
            return int(row[0])
        oid = int((await s.execute(sa.insert(oc_t).values(
            session_id=session_id, task_id=task_id, run_id=run_id,
            project_path=project_path, worktree_path=worktree_path,
            status=status, created_at=utcnow(), updated_at=utcnow(),
        ))).inserted_primary_key[0])
        await s.commit()
    return oid


async def set_status(svc, session_id: str, status: str) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(oc_t).where(oc_t.c.session_id == session_id)
                        .values(status=status, updated_at=utcnow()))
        await s.commit()


async def find_session(svc, session_id: str = "", *, run_id=None,
                       task_id=None) -> dict | None:
    """Строка маппинга: по id сессии, иначе последняя сессия этого run'а/задачи."""
    query = sa.select(oc_t).order_by(oc_t.c.id.desc()).limit(1)
    if session_id:
        query = sa.select(oc_t).where(oc_t.c.session_id == session_id).limit(1)
        # F-011: явный session_id — только своей задачи (чужую сессию не отдаём)
        if task_id is not None:
            query = query.where(oc_t.c.task_id == task_id)
    elif run_id is not None:
        query = query.where(oc_t.c.run_id == run_id)
    elif task_id is not None:
        query = query.where(oc_t.c.task_id == task_id)
    else:
        return None
    async with svc.db.session() as s:
        row = (await s.execute(query)).first()
    return dict(row._mapping) if row else None


async def persist_diff(svc, run_id, session_id: str, diffs: list[dict]) -> None:
    """Снимок диффа в журнал run'а — он переживает рестарт.

    Своей колонки под артефакты у `opencode_sessions` нет (схему меняет только
    Integration Lead), поэтому пишем в каноничный `run_events`. Заявка на
    колонку `meta JSON` — в заметках лейна.
    """
    if run_id is None:
        return
    summary = diff_summary(diffs)
    async with svc.db.session() as s:
        await s.execute(sa.insert(run_events).values(
            run_id=run_id, ts=utcnow(), level="info", kind="opencode.diff",
            message=f"OpenCode {session_id}: файлов {summary['files']}, "
                    f"+{summary['additions']}/-{summary['deletions']}",
            data={"session_id": session_id, "summary": summary, "diff": diffs}))
        await s.commit()


async def load_diff(svc, run_id, session_id: str = "") -> list[dict]:
    """Последний сохранённый дифф run'а (для UI/Governor после рестарта)."""
    query = (sa.select(run_events.c.data)
             .where(run_events.c.run_id == run_id)
             .where(run_events.c.kind == "opencode.diff")
             .order_by(run_events.c.id.desc()))
    async with svc.db.session() as s:
        for row in (await s.execute(query)).fetchall():
            data = row[0] if isinstance(row[0], dict) else {}
            if session_id and data.get("session_id") != session_id:
                continue
            diff = data.get("diff")
            return diff if isinstance(diff, list) else []
    return []


# ------------------------------------------------------------- инструменты

def _unavailable(exc: Exception, name: str) -> ToolResult:
    return ToolResult(
        content=f"OpenCode недоступен ({type(exc).__name__}: {exc}). "
                f"Нужен запущенный `opencode serve`. Это состояние среды — "
                f"не выдумывайте результат работы агента.",
        one_line=f"{name}: OpenCode недоступен", error=True)


async def _resolve(ctx, args: dict, name: str):
    """Строка маппинга для follow-up вызовов. Каталог берём ИЗ БД, не из модели."""
    row = await find_session(ctx.svc, str(args.get("session_id") or ""),
                             run_id=ctx.run_id,
                             task_id=(ctx.task or {}).get("id"))
    if not row:
        return None, ToolResult(
            content="нет привязанной сессии OpenCode: сначала opencode.session.start",
            one_line=f"{name}: нет сессии", error=True)
    return row, None


def _directory_of(row: dict) -> str:
    return str(row.get("worktree_path") or row.get("project_path") or "")


async def _tool_start(args: dict, ctx) -> ToolResult:
    raw = str(args.get("project_path") or ctx.workspace or "")
    project, refusal = await approved_dir(ctx.svc, raw)
    if project is None:
        return ToolResult(content=f"отказ: {refusal}",
                          one_line="opencode.session.start: путь не одобрен", error=True)

    worktree = project
    if args.get("worktree"):
        name = f"run{ctx.run_id or 0}"
        made, err = await make_worktree(project, name)
        if made is None:
            return ToolResult(content=f"отказ: {err}",
                              one_line="opencode.session.start: worktree не создан",
                              error=True)
        # worktree лежит рядом с проектом — он тоже обязан быть внутри корней
        checked, refusal = await approved_dir(ctx.svc, str(made))
        if checked is None:
            return ToolResult(content=f"отказ: worktree вне одобренных корней ({refusal})",
                              one_line="opencode.session.start: worktree вне корней",
                              error=True)
        worktree = checked

    bridge = bridge_for(ctx.svc)
    try:
        session = await bridge.create_session(
            str(worktree), title=str(args.get("title") or (ctx.task or {}).get("title") or ""),
            agent=str(args.get("agent") or ""))
    except (httpx.HTTPError, OSError) as exc:
        return _unavailable(exc, "opencode.session.start")
    session_id = str(session.get("id") or "")
    if not session_id:
        return ToolResult(content=f"OpenCode не вернул id сессии: {session}",
                          one_line="opencode.session.start: нет id", error=True)

    await record_session(ctx.svc, session_id=session_id,
                         task_id=(ctx.task or {}).get("id"), run_id=ctx.run_id,
                         project_path=str(project), worktree_path=str(worktree))
    return ToolResult(
        content=f"сессия OpenCode {session_id} запущена в {worktree}. "
                f"Дальше: opencode.send с текстом задания.",
        one_line=f"opencode.session.start: {session_id}",
        data={"session_id": session_id, "directory": str(worktree),
              "project_path": str(project)})


async def _tool_send(args: dict, ctx) -> ToolResult:
    text = str(args.get("text") or "").strip()
    if not text:
        return ToolResult(content="нужен аргумент text — что именно должен сделать OpenCode",
                          one_line="opencode.send: нет задания", error=True)
    row, err = await _resolve(ctx, args, "opencode.send")
    if err:
        return err
    session_id = str(row["session_id"])
    directory = _directory_of(row)
    bridge = bridge_for(ctx.svc)
    wait = args.get("wait")
    wait = True if wait is None else bool(wait)
    try:
        if not wait:
            await bridge.prompt_async(session_id, text, directory)
            await set_status(ctx.svc, session_id, "running")
            return ToolResult(
                content=f"задание отдано сессии {session_id}, ответа не ждём. "
                        f"Проверяйте opencode.status.",
                one_line=f"opencode.send: отдано ({session_id})",
                data={"session_id": session_id, "wait": False})
        reply = await bridge.send_message(session_id, text, directory)
    except (httpx.HTTPError, OSError) as exc:
        return _unavailable(exc, "opencode.send")
    await set_status(ctx.svc, session_id, "active")
    body = assistant_text(reply)
    return ToolResult(
        content=body or "OpenCode завершил шаг без текстового ответа",
        one_line=f"opencode.send: ответ получен ({session_id})",
        data={"session_id": session_id,
              "message_id": str((reply.get("info") or {}).get("id") or "")},
        external=True)


async def _tool_status(args: dict, ctx) -> ToolResult:
    row, err = await _resolve(ctx, args, "opencode.status")
    if err:
        return err
    session_id = str(row["session_id"])
    directory = _directory_of(row)
    bridge = bridge_for(ctx.svc)
    try:
        state = await bridge.session_status(session_id, directory)
        todo = await bridge.todo(session_id, directory)
    except (httpx.HTTPError, OSError) as exc:
        return _unavailable(exc, "opencode.status")
    kind = str(state.get("type") or "idle")
    lines = [f"session_id={session_id}", f"состояние={kind}",
             f"каталог={directory}", f"запись в БД={row.get('status')}"]
    for item in todo:
        if isinstance(item, dict):
            lines.append(f"- [{item.get('status')}] {item.get('content')}")
    return ToolResult(content="\n".join(lines),
                      one_line=f"opencode.status: {kind}",
                      data={"session_id": session_id, "state": kind,
                            "todo": todo, "db_status": row.get("status")})


async def _tool_diff(args: dict, ctx) -> ToolResult:
    row, err = await _resolve(ctx, args, "opencode.diff")
    if err:
        return err
    session_id = str(row["session_id"])
    directory = _directory_of(row)
    bridge = bridge_for(ctx.svc)
    try:
        diffs = await bridge.diff(session_id, str(args.get("message_id") or "") or None,
                                  directory)
    except (httpx.HTTPError, OSError) as exc:
        return _unavailable(exc, "opencode.diff")
    await persist_diff(ctx.svc, ctx.run_id, session_id, diffs)
    summary = diff_summary(diffs)
    if not diffs:
        return ToolResult(content="OpenCode пока не изменил ни одного файла",
                          one_line="opencode.diff: изменений нет",
                          data={"session_id": session_id, "summary": summary})
    text, truncated = render_diff(diffs, DIFF_LIMIT)
    head = (f"файлов {summary['files']}, +{summary['additions']}/-{summary['deletions']}: "
            f"{', '.join(summary['paths'])}")
    return ToolResult(
        content=f"{head}\n\n{text}",
        one_line=f"opencode.diff: {summary['files']} файл(ов)",
        truncated=truncated,
        more="opencode.diff с message_id конкретного сообщения" if truncated else "",
        data={"session_id": session_id, "summary": summary}, external=True)


async def _tool_abort(args: dict, ctx) -> ToolResult:
    row, err = await _resolve(ctx, args, "opencode.abort")
    if err:
        return err
    session_id = str(row["session_id"])
    bridge = bridge_for(ctx.svc)
    try:
        ok = await bridge.abort(session_id, _directory_of(row))
    except (httpx.HTTPError, OSError) as exc:
        return _unavailable(exc, "opencode.abort")
    await set_status(ctx.svc, session_id, "aborted" if ok else str(row.get("status")))
    await ctx.svc.bus.emit("agent.warning", tool="opencode.abort", session_id=session_id)
    return ToolResult(content=f"сессия {session_id} остановлена" if ok
                      else f"OpenCode не подтвердил остановку {session_id}",
                      one_line=f"opencode.abort: {'ок' if ok else 'не подтверждено'}",
                      data={"session_id": session_id, "aborted": bool(ok)})


# ------------------------------------------------------------------ политика

def _autonomous(_args: dict) -> tuple[str, str]:
    """Запуск автономного кодинг-агента всегда идёт через человека.

    Хук может только УЖЕСТОЧИТЬ решение: даже агент с правом terminal.run не
    получает AUTO на OpenCode. Ослабить может лишь явное правило пользователя
    в `agents.permissions.tool_rules` — это осознанное решение человека.
    """
    return "ask", "запуск автономного кодинг-агента OpenCode"


SPECS = [
    ToolSpec(
        name="opencode.session.start",
        description=("Открыть сессию автономного кодинг-агента OpenCode в одобренном "
                     "каталоге проекта или отдельном git worktree. Доступ строго к "
                     "этому каталогу, не ко всему компьютеру."),
        handler=_tool_start,
        input_schema={
            "project_path": {"type": "string",
                             "description": "каталог проекта (по умолчанию — workspace задачи)"},
            "worktree": {"type": "boolean",
                         "description": "создать отдельный git worktree под задачу"},
            "title": {"type": "string", "description": "заголовок сессии"},
            "agent": {"type": "string", "description": "имя агента OpenCode"},
        },
        category="exec", permission="terminal.run", source="opencode",
        default_effect="ask", timeout_seconds=180.0, idempotent=False,
        effect_hook=_autonomous),
    ToolSpec(
        name="opencode.send",
        description=("Отправить задание в сессию OpenCode. wait=true — дождаться ответа, "
                     "wait=false — отдать задание и следить через opencode.status."),
        handler=_tool_send,
        input_schema={
            "text": {"type": "string", "description": "что именно сделать в коде"},
            "session_id": {"type": "string",
                           "description": "сессия (по умолчанию — последняя у этого run'а)"},
            "wait": {"type": "boolean", "description": "ждать ответа (по умолчанию да)"},
        },
        required=["text"], category="exec", permission="terminal.run", source="opencode",
        default_effect="ask", timeout_seconds=900.0, idempotent=False,
        external_output=True, effect_hook=_autonomous),
    ToolSpec(
        name="opencode.status",
        description="Состояние сессии OpenCode (idle/busy/retry) и её список задач.",
        handler=_tool_status,
        input_schema={"session_id": {"type": "string"}},
        category="read", permission="terminal.read", source="opencode",
        default_effect="auto", timeout_seconds=60.0),
    ToolSpec(
        name="opencode.diff",
        description=("Изменения, сделанные OpenCode в сессии: список файлов и патчи. "
                     "Снимок сохраняется в журнал run'а."),
        handler=_tool_diff,
        input_schema={"session_id": {"type": "string"},
                      "message_id": {"type": "string",
                                     "description": "дифф только по этому сообщению"}},
        category="read", permission="terminal.read", source="opencode",
        default_effect="auto", timeout_seconds=120.0, external_output=True),
    ToolSpec(
        name="opencode.abort",
        description="Остановить работу сессии OpenCode.",
        handler=_tool_abort,
        input_schema={"session_id": {"type": "string"}},
        category="exec", source="opencode",
        default_effect="auto", timeout_seconds=60.0, idempotent=False),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_opencode", setup=_setup)
