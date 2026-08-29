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
import re
import uuid

from .. import errors, obs
from ..config import settings
from . import ToolContext, ToolDef, ToolResult, clip, register

_log = obs.get_logger("bossman.toolkit.shell")


def _build_command(cmd: str, ctx: ToolContext) -> list[str]:
    """Собрать argv исполнителя либо отказать (fail closed).

    docker  → контейнер без сети, смонтирован только workdir; `cmd` попадает
              внутрь ЕДИНСТВЕННЫМ аргументом `sh -lc` контейнера — хостовый
              шелл строку не видит вообще (argv-only дисциплина Этапа 8);
    local   → хостовый `sh -c`, БЕЗ изоляции: только при BOSSMAN_UNSAFE_LOCAL_EXEC=1;
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
        return ["sh", "-c", cmd]
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
    log_path.write_text(out)
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
    log_path.write_text(out)
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


register(ToolDef("run", "Команда в sandbox (без сети, смонтирован только репозиторий).",
                 "exec", run, params={"cmd": {"type": "string"}, "timeout": {"type": "integer"}},
                 required=["cmd"], token_limit=3000))
register(ToolDef("tests", "Прогнать тесты; вернуть сводку прошло/упало и первую ошибку.",
                 "exec", tests, params={"cmd": {"type": "string"}}, token_limit=2000))
