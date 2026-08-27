"""fs.* — файлы внутри workdir агента. Лимиты из 10.4."""
from __future__ import annotations

import re
from pathlib import Path

from . import ToolContext, ToolDef, ToolResult, clip, register


def _resolve(ctx: ToolContext, rel: str) -> Path:
    p = (ctx.workdir / rel).resolve()
    if not str(p).startswith(str(ctx.workdir.resolve())):
        raise PermissionError(f"путь вне рабочей папки: {rel}")
    return p


async def fs_read(args: dict, ctx: ToolContext) -> ToolResult:
    path = _resolve(ctx, args["path"])
    if not path.exists():
        return ToolResult(f"нет файла: {args['path']}", one_line=f"нет файла {args['path']}", error=True)
    lines = path.read_text(errors="replace").splitlines()
    start = int(args.get("from", 1))
    end = min(int(args.get("to", start + 199)), start + 199, len(lines))  # ≤ 200 строк
    body = "\n".join(f"{i}\t{lines[i-1]}" for i in range(start, end + 1))
    body, cut = clip(body, 4000)
    truncated = cut or end < len(lines) or start > 1
    return ToolResult(body, one_line=f"прочитан {args['path']}:{start}-{end} из {len(lines)} строк",
                      truncated=truncated,
                      more=f"fs.read(path='{args['path']}', from={end+1}, to={min(end+200, len(lines))})"
                           if end < len(lines) else "")


async def fs_write(args: dict, ctx: ToolContext) -> ToolResult:
    path = _resolve(ctx, args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"])
    line = f"записан {args['path']} ({len(args['content'])} байт)"
    return ToolResult(line, one_line=line)


async def fs_edit(args: dict, ctx: ToolContext) -> ToolResult:
    """Замена точного фрагмента (diff-подход: старое → новое, без переписывания файла)."""
    path = _resolve(ctx, args["path"])
    text = path.read_text()
    old = args["old"]
    if text.count(old) != 1:
        return ToolResult(f"фрагмент найден {text.count(old)} раз — нужен уникальный",
                          one_line=f"edit {args['path']}: фрагмент не уникален", error=True)
    path.write_text(text.replace(old, args["new"], 1))
    line = f"правка в {args['path']}"
    return ToolResult(line, one_line=line)


async def fs_search(args: dict, ctx: ToolContext) -> ToolResult:
    """grep: путь + номер строки + одна строка совпадения; ≤ 50 совпадений."""
    pattern = re.compile(args["pattern"])
    offset = int(args.get("offset", 0))
    hits: list[str] = []
    skipped = 0
    for f in sorted(ctx.workdir.rglob(args.get("glob", "*"))):
        if not f.is_file() or f.stat().st_size > 2_000_000:
            continue
        try:
            for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    if skipped < offset:
                        skipped += 1
                        continue
                    hits.append(f"{f.relative_to(ctx.workdir)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 50:
                        body = "\n".join(hits)
                        return ToolResult(body, one_line=f"поиск '{args['pattern']}': ≥50 совпадений",
                                          truncated=True,
                                          more=f"fs.search(pattern=…, offset={offset + 50})")
        except OSError:
            continue
    body = "\n".join(hits) or "совпадений нет"
    return ToolResult(body, one_line=f"поиск '{args['pattern']}': {len(hits)} совпадений")


async def fs_list(args: dict, ctx: ToolContext) -> ToolResult:
    """Имена и размеры, без рекурсии; ≤ 100 записей."""
    base = _resolve(ctx, args.get("path", "."))
    depth = int(args.get("depth", 1))
    offset = int(args.get("offset", 0))
    entries: list[str] = []
    def walk(d: Path, level: int) -> None:
        for p in sorted(d.iterdir()):
            rel = p.relative_to(ctx.workdir)
            entries.append(f"{rel}/" if p.is_dir() else f"{rel} ({p.stat().st_size} Б)")
            if p.is_dir() and level < depth:
                walk(p, level + 1)
    walk(base, 1)
    page = entries[offset:offset + 100]
    truncated = len(entries) > offset + 100
    return ToolResult("\n".join(page) or "пусто",
                      one_line=f"список {args.get('path', '.')}: {len(entries)} записей",
                      truncated=truncated,
                      more=f"fs.list(path='{args.get('path', '.')}', offset={offset + 100})" if truncated else "")


P = {"path": {"type": "string"}}
register(ToolDef("fs.read", "Диапазон строк файла с номерами (≤200 строк). Сначала оглавление/поиск, потом диапазон.",
                 "read", fs_read, params={**P, "from": {"type": "integer"}, "to": {"type": "integer"}},
                 required=["path"], token_limit=4000))
register(ToolDef("fs.write", "Создать или перезаписать файл целиком.", "write", fs_write,
                 params={**P, "content": {"type": "string"}}, required=["path", "content"]))
register(ToolDef("fs.edit", "Точечная правка: заменить уникальный фрагмент old на new.", "write", fs_edit,
                 params={**P, "old": {"type": "string"}, "new": {"type": "string"}},
                 required=["path", "old", "new"]))
register(ToolDef("fs.search", "Поиск по файлам (regex): путь, номер строки, строка совпадения (≤50).",
                 "read", fs_search,
                 params={"pattern": {"type": "string"}, "glob": {"type": "string"}, "offset": {"type": "integer"}},
                 required=["pattern"], token_limit=2000))
register(ToolDef("fs.list", "Список файлов: имена и размеры, без рекурсии (≤100 записей).", "read", fs_list,
                 params={**P, "depth": {"type": "integer"}, "offset": {"type": "integer"}}, token_limit=1500))
