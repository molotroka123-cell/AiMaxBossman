"""fs.* — файлы внутри workdir агента. Лимиты из 10.4."""
from __future__ import annotations

import re
from pathlib import Path

from . import ToolContext, ToolDef, ToolResult, clip, register


def _contains(root: Path, p: Path) -> bool:
    """True, если p — сам корень или лежит ВНУТРИ него.

    Проверка через отношение путей (relative_to/parents), а НЕ по префиксу строки:
    str.startswith пропускал соседа с общим префиксом имени (workdir `.../coder`
    считал `.../coder-secrets` «внутри»). Символические ссылки/junction ловятся
    тем, что оба пути уже .resolve()-нуты вызывающим (реальная цель сравнивается)."""
    return p == root or root in p.parents


def _resolve(ctx: ToolContext, rel: str) -> Path:
    root = ctx.workdir.resolve()
    p = (ctx.workdir / rel).resolve()
    if not _contains(root, p):
        raise PermissionError(f"путь вне рабочей папки: {rel}")
    return p


async def fs_read(args: dict, ctx: ToolContext) -> ToolResult:
    path = _resolve(ctx, args["path"])
    if not path.exists():
        return ToolResult(f"нет файла: {args['path']}", one_line=f"нет файла {args['path']}", error=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
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
    path.write_text(args["content"], encoding="utf-8")
    line = f"записан {args['path']} ({len(args['content'])} байт)"
    return ToolResult(line, one_line=line)


async def fs_edit(args: dict, ctx: ToolContext) -> ToolResult:
    """Замена точного фрагмента (diff-подход: старое → новое, без переписывания файла)."""
    path = _resolve(ctx, args["path"])
    text = path.read_text(encoding="utf-8", errors="replace")
    old = args["old"]
    if text.count(old) != 1:
        return ToolResult(f"фрагмент найден {text.count(old)} раз — нужен уникальный",
                          one_line=f"edit {args['path']}: фрагмент не уникален", error=True)
    path.write_text(text.replace(old, args["new"], 1), encoding="utf-8")
    line = f"правка в {args['path']}"
    return ToolResult(line, one_line=line)


async def fs_search(args: dict, ctx: ToolContext) -> ToolResult:
    """grep: путь + номер строки + одна строка совпадения; ≤ 50 совпадений."""
    pattern = re.compile(args["pattern"])
    offset = int(args.get("offset", 0))
    hits: list[str] = []
    skipped = 0
    root = ctx.workdir.resolve()
    # Инвариант containment: glob НЕ доверяем. rglob('../../*') и directory-
    # junction внутри workdir выводят пути наружу; каждый кандидат сверяем с
    # корнем по реальной цели (.resolve), иначе fs.search читал бы любой файл
    # процесса (Fable5.1 red-team F-001).
    for f in sorted(ctx.workdir.rglob(args.get("glob", "*"))):
        try:
            real = f.resolve()
        except OSError:
            continue
        if not _contains(root, real):
            continue
        if not f.is_file() or f.stat().st_size > 2_000_000:
            continue
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8",
                                                 errors="replace").splitlines(), 1):
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
    root = ctx.workdir.resolve()
    def walk(d: Path, level: int) -> None:
        for p in sorted(d.iterdir()):
            rel = p.relative_to(ctx.workdir)
            entries.append(f"{rel}/" if p.is_dir() else f"{rel} ({p.stat().st_size} Б)")
            # В директорию не рекурсируем, если её реальная цель выходит за workdir
            # (junction/symlink внутрь чужого каталога) — иначе fs.list перечислял
            # бы содержимое вне рабочей папки (Fable5.1 red-team F-002).
            if p.is_dir() and level < depth and _contains(root, p.resolve()):
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
