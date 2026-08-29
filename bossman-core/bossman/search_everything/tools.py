"""Регистрация search.*-инструментов в общий REGISTRY toolkit'а.

Инструменты добавляются через публичный register() toolkit'а (сам toolkit не
правим). Импорт toolkit — ленивый (внутри register_tools), чтобы
`import bossman.search_everything` не тянул весь toolkit/браузер.

Инструменты идут через SearchService → единый стор context_engine с sensitivity
allow-list по умолчанию: агент не получает секретов из поиска, а search.index
отклоняет секрет-подобный текст.
"""
from __future__ import annotations

from .. import errors
from ..obs import get_logger
from .service import get_service

log = get_logger("bossman.search.tools")

_REGISTERED = False


def register_tools() -> None:
    """Идемпотентно зарегистрировать search.query и search.index."""
    global _REGISTERED
    if _REGISTERED:
        return
    from ..toolkit import REGISTRY, ToolDef, ToolResult, clip, compact_json, register

    if "search.query" in REGISTRY and "search.index" in REGISTRY:
        _REGISTERED = True
        return

    async def _query(args: dict, ctx) -> "ToolResult":  # noqa: ANN001
        q = str(args.get("q") or args.get("query") or "").strip()
        if not q:
            return ToolResult("пустой запрос", one_line="search.query: пустой запрос", error=True)
        project = str(args.get("project") or "")
        try:
            limit = max(1, min(50, int(args.get("limit", 8))))
        except (TypeError, ValueError):
            limit = 8
        raw_allow = args.get("sensitivity_allow")
        allow = tuple(raw_allow) if isinstance(raw_allow, (list, tuple)) and raw_allow else None
        try:
            hits = get_service().search(q, project=project, limit=limit, sensitivity_allow=allow)
        except errors.SearchFailed as exc:
            return ToolResult(f"поиск не выполнен: {exc.detail}",
                              one_line="search.query: сбой поиска", error=True)
        payload = [
            {
                "id": h.document.id,
                "source": h.document.source,
                "score": h.score,
                "project": h.document.project,
                "snippet": h.document.text[:280],
                "chunk_id": h.document.metadata.get("chunk_id"),
                "content_hash": h.document.metadata.get("content_hash"),
            }
            for h in hits
        ]
        body, cut = clip(compact_json({"count": len(payload), "hits": payload}), 3500)
        return ToolResult(
            body,
            one_line=f"search.query: {len(payload)} совпадений по '{q[:40]}'",
            truncated=cut,
            more="search.query(q=..., limit=меньше)" if cut else "",
        )

    async def _index(args: dict, ctx) -> "ToolResult":  # noqa: ANN001
        text = args.get("text")
        uri = args.get("source_uri") or args.get("id")
        if not text or not uri:
            return ToolResult("нужны text и source_uri",
                              one_line="search.index: не хватает аргументов", error=True)
        doc = get_service().index_text(
            str(text), source_uri=str(uri), source_type=str(args.get("source", "text")),
            project=str(args.get("project", "")), sensitivity=str(args.get("sensitivity", "normal")),
        )
        if doc is None:
            return ToolResult("отклонено: секрет не индексируется",
                              one_line="search.index: отказ (секрет/без изменений)", error=True)
        return ToolResult(f"проиндексировано: {uri}", one_line=f"search.index: {uri}")

    register(ToolDef(
        "search.query",
        "Поиск по единому индексу знаний (context_engine): чанки с provenance и score. Секреты не возвращаются.",
        "read", _query,
        params={
            "q": {"type": "string"},
            "project": {"type": "string"},
            "limit": {"type": "integer"},
        },
        required=["q"], token_limit=3500,
    ))
    register(ToolDef(
        "search.index",
        "Проиндексировать текст в единый индекс (context_engine). Секрет-подобное содержимое отклоняется.",
        "write", _index,
        params={
            "text": {"type": "string"},
            "source_uri": {"type": "string"},
            "source": {"type": "string"},
            "project": {"type": "string"},
            "sensitivity": {"type": "string"},
        },
        required=["text", "source_uri"], token_limit=1000, confirm_default=False,
    ))
    _REGISTERED = True
    log.info("search: инструменты search.query/search.index зарегистрированы")
