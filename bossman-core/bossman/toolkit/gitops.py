"""git — status, diff, branch, commit внутри workdir. Diff ≤ 4K токенов, по файлам."""
from __future__ import annotations

import asyncio
import shlex

from . import ToolContext, ToolDef, ToolResult, clip, register

ALLOWED = {"status", "diff", "branch", "commit", "log", "add", "checkout"}


async def _git(ctx: ToolContext, *argv: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        f"git -C {shlex.quote(str(ctx.workdir))} " + " ".join(shlex.quote(a) for a in argv),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def git(args: dict, ctx: ToolContext) -> ToolResult:
    op = args["op"]
    if op not in ALLOWED:
        return ToolResult(f"операция '{op}' не разрешена ({', '.join(sorted(ALLOWED))})",
                          one_line=f"git {op}: отказ", error=True)
    extra: list[str] = [a for a in (args.get("args") or []) if not a.startswith("-") or a in
                        ("-m", "--stat", "-b", "--cached")]
    if op == "diff" and not args.get("files"):
        # статистика по файлам вместо полного diff; конкретный файл — через files
        code, out = await _git(ctx, "diff", "--stat", *extra)
    elif op == "diff":
        code, out = await _git(ctx, "diff", *extra, "--", *args["files"])
    elif op == "commit":
        code, out = await _git(ctx, "commit", "-m", args.get("message", "правки агента"))
    elif op == "log":
        code, out = await _git(ctx, "log", "--oneline", "-20")
    else:
        code, out = await _git(ctx, op, *extra)
    body, cut = clip(out or "(пусто)", 4000)
    return ToolResult(body, one_line=f"git {op} → код {code}", truncated=cut,
                      more="git(op='diff', files=[…]) по одному файлу" if cut else "",
                      error=code != 0)


register(ToolDef(
    "git", "git внутри рабочей папки: status | diff (без files — только статистика) | "
           "branch | commit | log | add | checkout.",
    "write", git,
    params={"op": {"type": "string", "enum": sorted(ALLOWED)},
            "args": {"type": "array", "items": {"type": "string"}},
            "files": {"type": "array", "items": {"type": "string"}},
            "message": {"type": "string"}},
    required=["op"], token_limit=4000))
