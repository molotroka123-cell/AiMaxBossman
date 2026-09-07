"""git — status, diff, branch, commit внутри workdir. Diff ≤ 4K токенов, по файлам."""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import ToolContext, ToolDef, ToolResult, clip, register
from .files import _contains

ALLOWED = {"status", "diff", "branch", "commit", "log", "add", "checkout"}


class GitAuthorityError(PermissionError):
    """Репозиторий, на который смотрит git, — не тот, что разрешён агенту."""


async def _exec(*argv: str, cwd: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def _authorized_root(ctx: ToolContext) -> Path:
    """Верхний уровень репозитория ОБЯЗАН совпасть с разрешённой папкой.

    RT-01: `git -C <workdir>` не значит «репозиторий = workdir». Git сам идёт
    вверх по родителям в поисках .git, поэтому рабочая папка агента, вложенная в
    больший репозиторий, давала команду НАД родительским репозиторием: `git
    checkout <чужая-ветка>` переключал его, `git add ../../file` индексировал
    файл снаружи. Полномочие должно быть явным, а не унаследованным вверх по
    дереву: спрашиваем сам git, что он считает верхним уровнем, и сверяем.
    """
    root = ctx.workdir.resolve()
    code, out = await _exec("git", "rev-parse", "--show-toplevel", cwd=root)
    if code != 0:
        raise GitAuthorityError("рабочая папка не является git-репозиторием")
    try:
        top = Path(out.strip()).resolve()
    except (OSError, RuntimeError) as exc:
        raise GitAuthorityError("не удалось разрешить корень репозитория") from exc
    if top != root:
        raise GitAuthorityError(
            f"репозиторий агенту не разрешён: git видит {top}, разрешено {root}")
    return root


def _pathspec_ok(root: Path, spec: str) -> bool:
    """Путь-аргумент обязан РЕАЛЬНО остаться внутри разрешённого репозитория."""
    if not spec or "\x00" in spec:
        return False
    if spec.startswith("-"):
        return False
    raw = root / spec
    try:
        if raw.exists() or raw.is_symlink():
            return _contains(root, raw.resolve()) or raw.resolve() == root
        parent = raw.parent.resolve()
        return _contains(root, parent) or parent == root
    except (OSError, RuntimeError):
        return False


async def _git(ctx: ToolContext, *argv: str) -> tuple[int, str]:
    # argv-only, без шелла: аргументы агента попадают в exec как есть, никакая
    # строка не интерпретируется. Дисциплина Этапа 8 действует и на хосте.
    #
    # Полномочие проверяется ПЕРЕД каждой операцией, а не один раз при создании
    # контекста: репозиторий под ногами может смениться между вызовами.
    root = await _authorized_root(ctx)
    return await _exec(
        "git", "--git-dir", str(root / ".git"), "--work-tree", str(root), *argv,
        cwd=root)


async def git(args: dict, ctx: ToolContext) -> ToolResult:
    op = args["op"]
    if op not in ALLOWED:
        return ToolResult(f"операция '{op}' не разрешена ({', '.join(sorted(ALLOWED))})",
                          one_line=f"git {op}: отказ", error=True)
    try:
        root = await _authorized_root(ctx)
    except GitAuthorityError as exc:
        return ToolResult(str(exc), one_line=f"git {op}: отказ по репозиторию", error=True)
    # `files` — всегда пути. В `args` путями являются только не-флаги: ветка для
    # checkout путём не является, поэтому проверяется лишь то, что существует
    # или указывает наружу как путь.
    for spec in list(args.get("files") or []):
        if not _pathspec_ok(root, str(spec)):
            return ToolResult(f"путь вне разрешённого репозитория: {spec}",
                              one_line=f"git {op}: отказ по пути", error=True)
    for a in list(args.get("args") or []):
        a = str(a)
        if a.startswith("-"):
            continue
        # Разделитель — не единственный признак пути: голое `link.txt` тоже путь,
        # если такой объект существует в репозитории. Ветка с тем же именем файла
        # не существует как файл, поэтому checkout по имени ветки не страдает.
        looks_like_path = "/" in a or "\\" in a
        try:
            exists = (root / a).exists() or (root / a).is_symlink()
        except (OSError, ValueError):
            exists = False
        if (looks_like_path or exists) and not _pathspec_ok(root, a):
            return ToolResult(f"путь вне разрешённого репозитория: {a}",
                              one_line=f"git {op}: отказ по пути", error=True)
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
