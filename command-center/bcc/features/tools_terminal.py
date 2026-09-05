"""V2.1 фаза B — Terminal как настоящий инструмент агента.

HTTP-страница терминала уже была; здесь тот же рантайм (bcc/v2/terminal_control)
подключается к каноническому реестру, чтобы МОДЕЛЬ могла запускать команды через
tool-loop с правами AUTO/ASK/DENY.

Политика (мастер-промпт §3):
  AUTO  — git status/diff/log, pytest, npm test, lint, build в разрешённом корне
  ASK   — git push, установка пакетов, docker compose, sudo, запись вне корня,
          включение сети в песочнице
  DENY  — force push, деструктивный reset, форматирование диска, дамп учёток,
          доступ к кошелькам, отключение защиты ОС

Режим по умолчанию — sandbox (docker, сеть выключена). Если docker недоступен,
инструмент честно возвращает ошибку, а не притворяется, что выполнил.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import sqlalchemy as sa

from ..db import settings_kv, utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2 import scratch
from ..v2.tables import terminal_sessions as term_t
from ..v2.terminal_control import TerminalManager, TerminalPolicy, within
from . import Feature

ROOTS_KEY = "terminal.roots"
MODE_KEY = "terminal.default_mode"
# Псевдоним личной рабочей области агента (V2.2 §9): модель называет
# её словом, а не путём — путей соседей она знать не должна.
SCRATCH_ALIAS = "scratch"
OUTPUT_LIMIT = 8000          # символов вывода в модель за один вызов

# Отдельный от TerminalPolicy слой: то, что нельзя НИКОГДА, даже с подтверждением.
HARD_DENY = [
    (re.compile(r"(?i)\bgit\s+push\b.*(?:--force|-f\b)"), "force push"),
    (re.compile(r"(?i)\bgit\s+reset\s+--hard\b"), "деструктивный reset"),
    (re.compile(r"(?i)\b(?:mkfs|fdisk|diskpart|format)\b"), "форматирование диска"),
    (re.compile(r"(?i)\brm\s+-rf\s+/(?:\s|$)"), "удаление корня"),
    (re.compile(r"(?i)(?:/etc/shadow|id_rsa|\.ssh/id_|\.aws/credentials|\.env\b)"),
     "доступ к учётным данным"),
    (re.compile(r"(?i)\b(?:wallet|keystore|seed[_-]?phrase|private[_-]?key)\b"),
     "доступ к кошельку/ключам"),
    (re.compile(r"(?i)\b(?:setenforce\s+0|ufw\s+disable|systemctl\s+stop\s+(?:firewalld|apparmor))\b"),
     "отключение защиты ОС"),
]
ASK_EXTRA = [
    (re.compile(r"(?i)\b(?:npm|pnpm|yarn|pip|pip3|poetry|uv)\s+(?:install|add)\b"), "установка пакетов"),
    (re.compile(r"(?i)\bgit\s+push\b"), "публикация изменений"),
    (re.compile(r"(?i)\bdocker\s+compose\s+(?:up|down|restart)\b"), "управление сервисами"),
    (re.compile(r"(?i)\b(?:sudo|runas|su\s+-)\b"), "повышение прав"),
]


def _mgr(svc) -> TerminalManager:
    if getattr(svc, "terminal", None) is None:
        svc.terminal = TerminalManager()
    return svc.terminal


async def _roots(svc) -> list[Path]:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == ROOTS_KEY))).first()
    if row and row[0]:
        try:
            return [Path(p) for p in json.loads(svc.vault.decrypt(row[0]))]
        except Exception:
            pass
    return [svc.settings.data_dir]


async def _mode(svc) -> str:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == MODE_KEY))).first()
    if row and row[0]:
        try:
            return str(svc.vault.decrypt(row[0])) or "sandbox"
        except Exception:
            pass
    return "sandbox"


def hard_deny_reason(command: str) -> str:
    for pattern, reason in HARD_DENY:
        if pattern.search(command or ""):
            return reason
    return ""


def extra_ask_reason(command: str) -> str:
    for pattern, reason in ASK_EXTRA:
        if pattern.search(command or ""):
            return reason
    return ""


async def _resolve_cwd(ctx, args: dict) -> tuple[Path, list[Path]]:
    """Рабочий каталог вызова: workspace задачи → аргумент → корень по умолчанию.
    Выход за разрешённые корни — забота политики, здесь только вычисление.

    `cwd="scratch"` — единственный способ для модели назвать свою личную
    рабочую область, не зная и не угадывая её путь (V2.2 §9).
    """
    roots = await _roots(ctx.svc)
    raw = args.get("cwd") or ctx.workspace or (str(roots[0]) if roots else ".")
    if str(raw).strip() == SCRATCH_ALIAS:
        return scratch.ensure(scratch.for_context(ctx)), roots
    # F-009: резолвим ДО авторизации — symlink/junction/../ и кодированные
    # варианты сравниваются с корнями уже как канонический путь.
    return Path(raw).expanduser().resolve(), roots


def normalize_run_args(args: dict) -> dict:
    """F-013/F-009: канонические аргументы для approval-digest и предпросмотра.

    cwd резолвится так же, как при исполнении (_resolve_cwd), поэтому
    «одобренный путь == исполненный путь»; псевдоним scratch остаётся словом
    (он детерминированно отображается в личный каталог задачи, а не в
    произвольный путь). Никакого доступа к БД — функция чистая."""
    out = dict(args or {})
    raw = out.get("cwd")
    if raw and str(raw).strip() != SCRATCH_ALIAS:
        try:
            out["cwd"] = str(Path(str(raw)).expanduser().resolve())
        except OSError:
            out["cwd"] = str(raw)
    out["mode"] = str(out.get("mode") or "sandbox")
    return out


# ------------------------------------------------------------------ tools

async def _tool_run(args: dict, ctx) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult(content="нужен аргумент command", one_line="terminal.run: нет команды",
                          error=True)
    deny = hard_deny_reason(command)
    if deny:
        return ToolResult(content=f"команда запрещена без исключений ({deny}) — "
                                  f"не выполнять и не искать обход",
                          one_line=f"terminal.run: запрещено ({deny})", error=True)

    cwd, roots = await _resolve_cwd(ctx, args)
    # V2.2 §9: чужая рабочая область агента закрыта раньше проверки корней —
    # она внутри разрешённого корня, и общая проверка её бы пропустила.
    blocked = scratch.check(ctx, cwd)
    if blocked:
        return ToolResult(content=blocked, one_line="terminal.run: чужая рабочая область",
                          error=True)
    mode = str(args.get("mode") or await _mode(ctx.svc))
    if mode not in ("sandbox", "project_host", "system_admin"):
        mode = "sandbox"
    own_scratch = scratch.for_context(ctx)
    # F-009 (Fable 5.1 HIGH): раньше sandbox брал effective_roots=[cwd] — то есть
    # модель сама объявляла корнем ЛЮБОЙ каталог хоста и он уходил RW в
    # `-v cwd:/work` без approval. Теперь для ВСЕХ режимов корни одни:
    # разрешённые владельцем + личная scratch-область. Контейнер остаётся
    # защитой в глубину, но не заменяет авторизацию пути на хосте.
    effective_roots = list(roots)
    if own_scratch not in effective_roots:
        effective_roots.append(own_scratch)     # своя область доступна всегда
    if not within(cwd, roots) and not within(cwd, [own_scratch]):
        return ToolResult(content=f"каталог {cwd} вне разрешённых корней "
                                  f"({', '.join(str(r) for r in roots)})",
                          one_line="terminal.run: cwd вне корней", error=True)

    policy = TerminalPolicy(allowed_roots=effective_roots, mode=mode)
    if policy.decision(command, cwd) == "deny":
        return ToolResult(content="команда запрещена политикой терминала",
                          one_line="terminal.run: deny политики", error=True)

    timeout = float(args.get("timeout") or 120)
    try:
        session = await _mgr(ctx.svc).start(command, cwd, policy, approved=True,
                                            network=bool(args.get("network")),
                                            owner=str(ctx.task.get("id")))
    except PermissionError as exc:
        return ToolResult(content=f"отказ политики: {exc}", one_line="terminal.run: отказ",
                          error=True)
    except (FileNotFoundError, OSError) as exc:
        return ToolResult(
            content=f"не удалось запустить терминал в режиме {mode}: {exc}. "
                    f"Для sandbox нужен docker; попробуйте mode=project_host.",
            one_line="terminal.run: рантайм недоступен", error=True)

    async with ctx.svc.db.session() as s:
        await s.execute(sa.insert(term_t).values(
            id=session.id, mode=mode, cwd=str(cwd), command=command, status="running",
            pid=session.proc.pid, started_at=utcnow(),
            task_id=ctx.task.get("id"), agent_id=(ctx.agent or {}).get("id")))
        await s.commit()

    deadline = asyncio.get_running_loop().time() + timeout
    while not session.finished and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)

    output = "\n".join(session.output)
    truncated = len(output) > OUTPUT_LIMIT
    if truncated:
        output = output[-OUTPUT_LIMIT:]
    if not session.finished:
        return ToolResult(
            content=f"команда всё ещё выполняется (session_id={session.id}), "
                    f"вывод на данный момент:\n{output}",
            one_line=f"terminal.run: выполняется ({session.id})",
            truncated=True, more=f"terminal.status с session_id={session.id}",
            data={"session_id": session.id, "finished": False}, external=True)

    async with ctx.svc.db.session() as s:
        await s.execute(sa.update(term_t).where(term_t.c.id == session.id).values(
            status="finished", exit_code=session.exit_code, finished_at=utcnow()))
        await s.commit()
    # A started process is not a successful command. Keep the process outcome
    # as structured data, while propagating failure to the engine's receipt
    # and completion checks (including failed tests and linters).
    return ToolResult(
        content=f"exit_code={session.exit_code}\n{output}",
        one_line=f"terminal.run: exit={session.exit_code}",
        truncated=truncated,
        error=session.exit_code != 0,
        more=f"terminal.status с session_id={session.id}" if truncated else "",
        data={"session_id": session.id, "exit_code": session.exit_code}, external=True)


def _owned(ctx, mgr: TerminalManager, sid: str) -> str:
    """F-011: сессия по session_id доступна только задаче-владельцу.
    Пустая строка = ок, иначе текст отказа."""
    sess = mgr.sessions.get(sid)
    if sess is None:
        return f"сессия {sid} не найдена"
    owner = getattr(sess, "owner", None)
    if owner is not None and owner != str(ctx.task.get("id")):
        return f"сессия {sid} принадлежит другой задаче — доступ запрещён"
    return ""


async def _tool_status(args: dict, ctx) -> ToolResult:
    sid = str(args.get("session_id") or "")
    mgr = _mgr(ctx.svc)
    denied = _owned(ctx, mgr, sid)
    if denied:
        return ToolResult(content=denied, one_line="terminal.status: отказ", error=True)
    st = mgr.status(sid)
    tail = "\n".join(st["output_tail"])[-OUTPUT_LIMIT:]
    return ToolResult(content=f"finished={st['finished']} exit_code={st['exit_code']}\n{tail}",
                      one_line=f"terminal.status: {'finished' if st['finished'] else 'running'}",
                      error=bool(st["finished"] and st["exit_code"] != 0),
                      data=st, external=True)


async def _tool_stdin(args: dict, ctx) -> ToolResult:
    sid = str(args.get("session_id") or "")
    denied = _owned(ctx, _mgr(ctx.svc), sid)
    if denied:
        return ToolResult(content=denied, one_line="terminal.stdin: отказ", error=True)
    try:
        await _mgr(ctx.svc).write_stdin(sid, str(args.get("text") or ""))
    except (KeyError, RuntimeError) as exc:
        return ToolResult(content=f"не удалось записать в stdin: {exc}",
                          one_line="terminal.stdin: ошибка", error=True)
    return ToolResult(content="ввод отправлен", one_line="terminal.stdin: ок")


async def _tool_kill(args: dict, ctx) -> ToolResult:
    sid = str(args.get("session_id") or "")
    mgr = _mgr(ctx.svc)
    denied = _owned(ctx, mgr, sid)
    if denied:
        return ToolResult(content=denied, one_line="terminal.kill: отказ", error=True)
    await mgr.kill(sid)
    async with ctx.svc.db.session() as s:
        await s.execute(sa.update(term_t).where(term_t.c.id == sid).values(
            status="killed", finished_at=utcnow()))
        await s.commit()
    await ctx.svc.bus.emit("agent.warning", tool="terminal.kill", session_id=sid)
    return ToolResult(content="процесс остановлен", one_line="terminal.kill: ок")


def _run_effect(args: dict) -> tuple[str, str] | None:
    """Политика по самой команде. Может только ужесточить решение:
    выданное право `terminal.run` не превращает `git push` в AUTO."""
    command = str(args.get("command") or "")
    deny = hard_deny_reason(command)
    if deny:
        return "deny", f"деструктивная команда: {deny}"
    ask = extra_ask_reason(command)
    if ask:
        return "ask", f"требует подтверждения: {ask}"
    if args.get("network"):
        return "ask", "включение сети в песочнице"
    # A project-host command crosses the container boundary.  A granted
    # capability may enable the tool, but must never turn arbitrary host shell
    # execution into AUTO; only the persisted owner approval may do that.
    if args.get("mode") == "project_host":
        return "ask", "host shell always requires owner approval"
    if args.get("mode") == "system_admin":
        return "ask", "режим system_admin"
    # Читающие команды (git status/diff/log, pytest, npm test, линт, сборка)
    # ничем не ужесточаются: они идут AUTO, если агенту выдано право
    # terminal.run. Без права ASK остаётся на всё — это и есть безопасный
    # дефолт, а не оговорка.
    return None


SPECS = [
    ToolSpec(
        name="terminal.run",
        description=("Выполнить команду оболочки в разрешённом рабочем каталоге. "
                     "Читающие команды (git status/diff/log, pytest, npm test, линт, "
                     "сборка) выполняются сразу; установка пакетов, git push, docker "
                     "compose и sudo требуют подтверждения человека; деструктивные "
                     "команды запрещены."),
        handler=_tool_run,
        input_schema={
            "command": {"type": "string", "description": "команда целиком"},
            "cwd": {"type": "string", "description": "рабочий каталог (по умолчанию — workspace задачи); \"scratch\" — личная рабочая область этого агента, изолированная от других агентов миссии"},
            "mode": {"type": "string", "enum": ["sandbox", "project_host"],
                     "description": "sandbox — docker без сети (по умолчанию)"},
            "timeout": {"type": "number", "description": "сколько секунд ждать завершения"},
        },
        required=["command"], category="exec", permission="terminal.run", source="terminal",
        default_effect="ask", timeout_seconds=300.0, idempotent=False, external_output=True,
        effect_hook=_run_effect, normalize_args=normalize_run_args),
    ToolSpec(name="terminal.status",
             description="Состояние и вывод ранее запущенной команды по session_id.",
             handler=_tool_status,
             input_schema={"session_id": {"type": "string"}}, required=["session_id"],
             category="read", permission="terminal.read", source="terminal",
             default_effect="auto", external_output=True),
    ToolSpec(name="terminal.stdin", description="Отправить текст в stdin запущенной команды.",
             handler=_tool_stdin,
             input_schema={"session_id": {"type": "string"}, "text": {"type": "string"}},
             required=["session_id", "text"], category="exec", permission="terminal.run",
             source="terminal", default_effect="ask", idempotent=False),
    ToolSpec(name="terminal.kill", description="Остановить запущенную команду по session_id.",
             handler=_tool_kill,
             input_schema={"session_id": {"type": "string"}}, required=["session_id"],
             category="exec", permission="terminal.run", source="terminal",
             default_effect="auto", idempotent=False),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_terminal", setup=_setup)
