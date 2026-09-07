"""run и tests — команды в sandbox: docker-контейнер без сети, смонтирован только workdir.
Результат: код выхода + первые 30 и последние 30 строк (≤3K токенов);
полный вывод — в assets/logs/<id>.txt, дочитывается через fs.read.

Изоляция здесь — не украшение. Команду в `run`/`tests` диктует модель, а её
контекст содержит недоверенное: содержимое репозитория, README, issue, веб-
страницу. Второй путь исполнения без изоляции сводил бы на нет Stage 8, поэтому
режим `local` (хостовый шелл) требует отдельного осознанного включения, а любое
незнакомое значение SANDBOX_MODE трактуется как отказ, а не как «ну пусть local».
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid

from .. import errors, obs
from ..config import settings
from . import ToolContext, ToolDef, ToolResult, clip, register

_log = obs.get_logger("bossman.toolkit.shell")


def _host_shell_prefix() -> list[str]:
    """Чем запускать команду НА ХОСТЕ (режим local). POSIX — как было, `sh -c`.

    На Windows `sh` нет вообще: стоковая система знает cmd.exe, и argv
    `["sh", "-c", ...]` даёт не «команду без юникс-утилит», а FileNotFoundError
    на КАЖДЫЙ вызов run/tests. Развилка — та же, что в
    bcc/v2/terminal_control.py::host_shell: если в PATH есть `sh` (он приходит с
    Git for Windows и стоит почти у всех, кто работает с git) — берём его, и
    команды агентов работают как на боевом Linux; нет — честно `%COMSPEC% /c`,
    а не отказ.

    Выбор интерпретатора не имеет отношения к тому, РАЗРЕШЕНА ли команда:
    гейты SANDBOX_MODE/BOSSMAN_UNSAFE_LOCAL_EXEC стоят выше и не трогаются.
    Оговорка про cmd.exe: он не разбирает argv по правилам CRT, поэтому команда
    с кавычками может доехать искажённой — путь Git-Bash от этого свободен.
    """
    if os.name != "nt":
        return ["sh", "-c"]
    sh = shutil.which("sh")
    if sh:
        return [sh, "-lc"]
    return [os.environ.get("COMSPEC") or "cmd.exe", "/c"]


def _build_command(cmd: str, ctx: ToolContext) -> list[str]:
    """Собрать argv исполнителя либо отказать (fail closed).

    docker  → контейнер без сети, смонтирован только workdir; `cmd` попадает
              внутрь ЕДИНСТВЕННЫМ аргументом `sh -lc` контейнера — хостовый
              шелл строку не видит вообще (argv-only дисциплина Этапа 8);
    local   → хостовый шелл (`sh -c`; на Windows — Git-Bash `sh -lc` либо
              `%COMSPEC% /c`), БЕЗ изоляции: только при BOSSMAN_UNSAFE_LOCAL_EXEC=1;
    иное    → PolicyDenied.
    """
    mode = (settings.sandbox_mode or "").strip().lower()
    if mode == "docker":
        return ["docker", "run", "--rm", "--network", "none",
                "-v", f"{ctx.workdir.resolve()}:/work", "-w", "/work",
                settings.sandbox_image, "sh", "-lc", cmd]
    if mode == "local":
        if not settings.allow_unsafe_local_exec:
            raise errors.PolicyDenied(
                "SANDBOX_MODE=local исполняет команду агента на хосте без изоляции; "
                "включите осознанно через BOSSMAN_UNSAFE_LOCAL_EXEC=1 либо "
                "используйте SANDBOX_MODE=docker")
        # Разработческий режим и он ЗНАЕТ, что он разработческий: пусть это видно
        # в журнале, а не только в .env, о котором через месяц никто не вспомнит.
        _log.warning("exec без изоляции: SANDBOX_MODE=local + BOSSMAN_UNSAFE_LOCAL_EXEC=1")
        return [*_host_shell_prefix(), cmd]
    raise errors.PolicyDenied(
        f"неизвестный SANDBOX_MODE={settings.sandbox_mode!r}: ожидается docker или local")


async def _exec(cmd: str, ctx: ToolContext, timeout: int = 600) -> tuple[int, str]:
    argv = _build_command(cmd, ctx)
    mode = (settings.sandbox_mode or "").strip().lower()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(ctx.workdir) if mode == "local" else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"таймаут {timeout}с"
    return proc.returncode or 0, out.decode(errors="replace")


def _head_tail(text: str, n: int = 30) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= 2 * n:
        return text, False
    return "\n".join(lines[:n] + [f"… [{len(lines) - 2*n} строк пропущено] …"] + lines[-n:]), True


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    code, out = await _exec(args["cmd"], ctx, timeout=int(args.get("timeout", 600)))
    log_id = uuid.uuid4().hex[:8]
    log_path = ctx.workdir / "assets" / "logs" / f"{log_id}.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Вывод subprocess'а — не наш текст: utf-8 фиксируем явно (на русской
    # Windows дефолт cp1251 не кодирует рамки/стрелки pytest'а), а errors=
    # "replace" спасает от одиночного суррогата в чужом выводе.
    log_path.write_text(out, encoding="utf-8", errors="replace")
    body, cut1 = _head_tail(out)
    body, cut2 = clip(body, 3000)
    body = f"код выхода: {code}\n{body}"
    rel = log_path.relative_to(ctx.workdir)
    return ToolResult(body, one_line=f"run `{args['cmd'][:60]}` → код {code}, лог {rel}",
                      truncated=cut1 or cut2, more=f"fs.read(path='{rel}')",
                      error=code != 0)


async def tests(args: dict, ctx: ToolContext) -> ToolResult:
    """Сводка: прошло/упало + имена упавших + первая ошибка каждого (≤2K токенов)."""
    cmd = args.get("cmd", "pytest -x -q")
    code, out = await _exec(cmd, ctx, timeout=int(args.get("timeout", 900)))
    log_id = uuid.uuid4().hex[:8]
    log_path = ctx.workdir / "assets" / "logs" / f"tests-{log_id}.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Вывод subprocess'а — не наш текст: utf-8 фиксируем явно (на русской
    # Windows дефолт cp1251 не кодирует рамки/стрелки pytest'а), а errors=
    # "replace" спасает от одиночного суррогата в чужом выводе.
    log_path.write_text(out, encoding="utf-8", errors="replace")
    failed = re.findall(r"(?m)^(?:FAILED|ERROR) (\S+)", out)
    tail = out.splitlines()[-3:]
    summary = ["итог: " + (" / ".join(tail) if tail else f"код {code}")]
    if failed:
        summary.append("упавшие: " + ", ".join(failed[:20]))
        first_err = re.search(r"(?ms)^_{5,}.*?(?=^_{5,}|\Z)", out)
        if first_err:
            summary.append(first_err.group(0)[:1500])
    body, cut = clip("\n".join(summary), 2000)
    rel = log_path.relative_to(ctx.workdir)
    return ToolResult(body, one_line=f"tests → код {code}, упало {len(failed)}",
                      truncated=True, more=f"fs.read(path='{rel}')", error=code != 0)


def _host_exec_needs_approval() -> bool:
    """host/local исполнение = ALWAYS ASK; изолированный docker (сеть none) = AUTO.

    Защита-в-глубину без убийства fast-path: pytest/npm внутри одноразового
    контейнера без сети идут AUTO, а исполнение на РЕАЛЬНОЙ машине всегда
    требует подтверждения владельца (Security Hardening V1.1, H3). Неизвестный
    режим трактуем как требующий approval (fail-closed).
    """
    return (settings.sandbox_mode or "").strip().lower() != "docker"


register(ToolDef("run", "Команда в sandbox (без сети, смонтирован только репозиторий).",
                 "exec", run, params={"cmd": {"type": "string"}, "timeout": {"type": "integer"}},
                 required=["cmd"], mandatory_confirm=_host_exec_needs_approval, token_limit=3000))
register(ToolDef("tests", "Прогнать тесты; вернуть сводку прошло/упало и первую ошибку.",
                 "exec", tests, params={"cmd": {"type": "string"}},
                 mandatory_confirm=_host_exec_needs_approval, token_limit=2000))
