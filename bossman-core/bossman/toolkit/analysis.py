"""analysis.run — Python/Data Analysis Runtime (V2.6, раздел 19).

Dataframes, статистика, расчёты — БЕЗ произвольного шелла: единственное, что
исполняется, — `python3 -c <code>`, и только по тому же sandbox-пути, что и
shell.run. Никакого второго пути исполнения: argv строится через
shell._build_command, поэтому docker-режим — контейнер без сети со
смонтированным workdir, local — только при BOSSMAN_UNSAFE_LOCAL_EXEC=1,
незнакомый SANDBOX_MODE — PolicyDenied (fail closed). Код агента попадает в
командную строку через shlex.quote ОДНИМ аргументом — шелл его не
интерпретирует.

Подтверждения — инвариант shell.run: docker (изолирован) = AUTO,
host/local = ALWAYS ASK (mandatory_confirm нельзя переотменить грантом агента).
"""
from __future__ import annotations

import shlex
import uuid

from . import ToolContext, ToolDef, ToolResult, clip, register
# Приватные помощники соседнего модуля того же пакета — осознанное переиспользование
# (repo practice): один sandbox-путь, одна дисциплина head/tail, один предикат approval.
from .shell import _build_command, _exec, _head_tail, _host_exec_needs_approval

MAX_TIMEOUT_S = 120


def _clamp_timeout(value) -> int:
    """timeout_s агента — в пределах [1, 120]; мусор → дефолт 60с."""
    try:
        t = int(value)
    except (TypeError, ValueError):
        return 60
    return max(1, min(t, MAX_TIMEOUT_S))


def _python_cmd(code: str) -> str:
    """Единственная исполняемая форма: python3 -c <code одним аргументом>."""
    return "python3 -c " + shlex.quote(code)


def build_python_argv(code: str, ctx: ToolContext) -> list[str]:
    """Argv исполнителя для данного кода — тем же _build_command, что и shell.run.

    Вынесено отдельно, чтобы конструкция команды была проверяема тестами без
    реального docker: видно и `--network none`, и что код — один argv-элемент.
    """
    return _build_command(_python_cmd(code), ctx)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    code_arg = str(args["code"])
    timeout = _clamp_timeout(args.get("timeout_s", 60))
    exit_code, out = await _exec(_python_cmd(code_arg), ctx, timeout=timeout)
    log_id = uuid.uuid4().hex[:8]
    log_path = ctx.workdir / "assets" / "logs" / f"analysis-{log_id}.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out)
    body, cut1 = _head_tail(out)
    body, cut2 = clip(body, 3000)
    body = f"код выхода: {exit_code}\n{body}"
    rel = log_path.relative_to(ctx.workdir)
    return ToolResult(body,
                      one_line=f"analysis.run → код {exit_code}, лог {rel}",
                      truncated=cut1 or cut2, more=f"fs.read(path='{rel}')",
                      error=exit_code != 0)


register(ToolDef(
    "analysis.run",
    "Python-расчёт в sandbox: dataframes/статистика/вычисления. Исполняется "
    "ТОЛЬКО python3 -c <code>, без произвольного шелла.",
    "exec", run,
    params={"code": {"type": "string"},
            "timeout_s": {"type": "integer", "maximum": MAX_TIMEOUT_S}},
    required=["code"],
    mandatory_confirm=_host_exec_needs_approval,
    token_limit=3000))
