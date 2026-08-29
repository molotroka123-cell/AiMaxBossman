"""FastAPI-роутер этапа 5: GET /search — единый поиск с provenance.

Возвращает чанки с source/score/chunk_id/content_hash. Использует allow-list по
умолчанию (несекретные уровни), поэтому СЫРОЙ секрет никогда не отдаётся в ответ.
Настоящий сбой конвейера → errors.SearchFailed (рендерится install_error_handlers).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from .. import errors
from .service import get_service

router = APIRouter(tags=["search"])


@router.get("/search")
async def http_search(
    q: str = Query(..., description="поисковый запрос"),
    project: str = Query("", description="ограничить проектом"),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    try:
        hits = get_service().search(q, project=project, limit=limit)
    except errors.BossmanError:
        raise
    except Exception as exc:  # noqa: BLE001 — настоящий сбой плана поиска
        raise errors.SearchFailed(f"search failed: {type(exc).__name__}") from exc
    return {
        "query": q,
        "project": project,
        "count": len(hits),
        "hits": [
            {
                "id": h.document.id,
                "source": h.document.source,
                "score": h.score,
                "project": h.document.project,
                "snippet": h.document.text[:400],
                "chunk_id": h.document.metadata.get("chunk_id"),
                "document_id": h.document.metadata.get("document_id"),
                "content_hash": h.document.metadata.get("content_hash"),
                "reasons": list(h.reasons),
            }
            for h in hits
        ],
    }
