"""log и search_journal — журнал агента/проекта.

Закончил подзадачу — одна-три строки в journal.md через `log`, детали забываются;
понадобятся — найдутся через `search_journal` (3–5 чанков по 200–400 токенов)."""
from __future__ import annotations

from datetime import datetime, timezone

from . import ToolContext, ToolDef, ToolResult, register
from ..context import estimate_tokens


def _journal(ctx: ToolContext):
    p = ctx.journal or (ctx.workdir / "journal.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def log(args: dict, ctx: ToolContext) -> ToolResult:
    p = _journal(ctx)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with p.open("a") as f:
        f.write(f"- {ts} [{ctx.agent}] {args['text'].strip()}\n")
    return ToolResult("ok", one_line="запись в журнал")


async def search_journal(args: dict, ctx: ToolContext) -> ToolResult:
    """Поиск по журналу и сводкам в notes/: подстрока без регистра, чанк = абзац.
    (RAG по pgvector подключается здесь же, когда bossman-embed поднят.)"""
    query = args["query"].lower()
    sources = [_journal(ctx)]
    if ctx.notes_dir and ctx.notes_dir.exists():
        sources += sorted(ctx.notes_dir.glob("*.md"))
    chunks: list[str] = []
    for src in sources:
        if not src.exists():
            continue
        for para in src.read_text().split("\n\n"):
            if query in para.lower():
                chunk = para.strip()
                while estimate_tokens(chunk) > 400:
                    chunk = chunk[: len(chunk) * 2 // 3]
                chunks.append(f"[{src.name}] {chunk}")
                if len(chunks) >= 5:
                    break
        if len(chunks) >= 5:
            break
    body = "\n\n".join(chunks) or "ничего не найдено"
    return ToolResult(body, one_line=f"поиск в журнале '{args['query']}': {len(chunks)} чанков",
                      truncated=len(chunks) >= 5, more="fs.read по пути из чанка")


register(ToolDef("log", "Записать итог подзадачи в журнал (1–3 строки).", "write", log,
                 params={"text": {"type": "string"}}, required=["text"], token_limit=100))
register(ToolDef("search_journal", "Найти в журнале и сводках, что уже сделано/решено.",
                 "read", search_journal, params={"query": {"type": "string"}},
                 required=["query"], token_limit=2000))
