"""Публичный API поиска — ТОНКИЙ адаптер поверх bossman.context_engine.

P0-инвариант этапа: здесь НЕТ второго RAG/векторного стора. SearchEngine не
держит собственный dict документов, собственный лексический скоринг и собственный
RRF. Вся индексация и поиск идут через context_engine:

    connectors → Ingestor (chunking) → ContextStore (единый SQLite/WAL индекс)
               → HybridRetriever (lexical + vector + rerank + dedup
                                  + sensitivity allow-list)

Классы SearchDocument / SearchHit / SearchEngine сохранены только как ФОРМА
результата (floor для acceptance-теста stage4_7): имена те же, но хранилище и
скоринг под капотом — context_engine, а не приватный словарь.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .. import errors
from ..context_engine import ContextEngine
from ..context_engine.models import Document, RetrievalHit
from ..context_engine.retrieval import LexicalReranker, Reranker
from ..context_engine.utils import sha256_text
from ..obs import get_logger
from .connectors import SecretPolicy

log = get_logger("bossman.search.engine")

# Уровни sensitivity, доступные вызывающему БЕЗ специального права. Всё, что
# помечено иначе (sensitive/secret/...), не выдаётся, пока caller не предъявит
# расширенный allow-list. Секрет-подобные файлы вообще не попадают в индекс.
DEFAULT_ALLOW: tuple[str, ...] = ("normal", "public")


@dataclass(slots=True)
class SearchDocument:
    """Форма входного документа (id/text/source/project/metadata).

    id → source_uri (provenance), source → source_type. Порядок полей совпадает
    с эталонным прототипом, чтобы SearchDocument("1","...","repo","p") собирался
    позиционно, как в acceptance-тесте.
    """

    id: str
    text: str
    source: str
    project: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class SearchHit:
    """Форма результата: .document (с .source) + score + reasons (provenance)."""

    document: SearchDocument
    score: float
    reasons: tuple[str, ...] = ()


class SafeReranker:
    """Обёртка реранкера с ДЕГРАДАЦИЕЙ: сбой реранкинга не роняет агента.

    INTEGRATION_GUIDE §5: reranker failure must degrade to hybrid search. При
    исключении внутреннего реранкера падаем на порядок по max(lexical, vector).
    """

    def __init__(self, inner: Reranker | None = None) -> None:
        self.inner: Reranker = inner or LexicalReranker()

    def rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        try:
            return self.inner.rerank(query, hits)
        except Exception as exc:  # noqa: BLE001 — деградация, а не падение
            log.warning("search: reranker failed (%s), degrading to hybrid order", type(exc).__name__)
            for h in hits:
                h.rerank_score = max(h.lexical_score, h.vector_score)
            return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


def to_search_hit(h: RetrievalHit) -> SearchHit:
    """RetrievalHit (context_engine) → SearchHit (публичная форма), с provenance."""
    c = h.chunk
    doc = SearchDocument(
        id=c.source_uri or c.document_id,
        text=c.text,
        source=c.source_type,
        project=c.project or None,
        metadata={
            **(dict(c.metadata) if c.metadata else {}),
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "content_hash": c.content_hash,
            "source_uri": c.source_uri,
            "heading": c.heading,
            "sensitivity": c.sensitivity,
        },
    )
    return SearchHit(document=doc, score=round(h.final_score, 6), reasons=tuple(h.reasons))


class SearchEngine:
    """Адаптер над ContextEngine (единый стор). Публичный floor поиска.

    По умолчанию строит собственный ContextEngine на временном/in-memory сторе,
    чтобы работать в тестах БЕЗ внешних сервисов и детерминированно (HashEmbedder).
    В продакшне ему передаётся общий per-process ContextEngine (get_engine).
    """

    def __init__(self, engine: ContextEngine | None = None, *, db_path: str | Path | None = None,
                 reranker: Reranker | None = None,
                 policy: SecretPolicy | None = None,
                 sensitivity_allow: Iterable[str] = DEFAULT_ALLOW) -> None:
        self._owns = engine is None
        if engine is None:
            path = db_path
            if path is None:
                tmp = Path(tempfile.mkdtemp(prefix="bossman_search_"))
                path = tmp / "context.db"
            engine = ContextEngine(path, reranker=SafeReranker(reranker))
        self._engine = engine
        self.policy = policy or SecretPolicy()
        self.default_allow = tuple(sensitivity_allow)

    @property
    def engine(self) -> ContextEngine:
        return self._engine

    # ---- индексация -----------------------------------------------------------
    def upsert(self, docs: Iterable[SearchDocument], *, skip_unchanged: bool = True) -> list[Document]:
        """Проиндексировать документы через Ingestor (chunking) в единый стор.

        Секрет-подобный документ ОТКЛОНЯЕТСЯ (не входит в стор). Инкрементально:
        неизменившийся по content_hash документ не переиндексируется (§6).
        """
        out: list[Document] = []
        for d in docs:
            doc = self._ingest_one(d, skip_unchanged=skip_unchanged)
            if doc is not None:
                out.append(doc)
        return out

    def _ingest_one(self, d: SearchDocument, *, skip_unchanged: bool) -> Document | None:
        text = d.text or ""
        meta = dict(d.metadata or {})
        # Гейт секретов на ingest: секрет никогда не индексируется молча.
        if self.policy.is_secret(path=d.id, text=text, source=d.source or ""):
            log.info("search: отказ индексировать секрет-подобный документ id=%s", d.id)
            return None
        sensitivity = str(meta.pop("sensitivity", None) or "normal")
        if skip_unchanged and self._unchanged(d.id, sha256_text(text)):
            log.debug("search: документ не изменился, пропуск переиндексации id=%s", d.id)
            return None
        return self._engine.index_text(
            text, source_uri=d.id, source_type=(d.source or "text"),
            project=(d.project or ""), metadata=meta, sensitivity=sensitivity,
        )

    def _unchanged(self, source_uri: str, content_hash: str) -> bool:
        try:
            row = self._engine.store.db.execute(
                "SELECT content_hash FROM documents WHERE source_uri=? LIMIT 1", (source_uri,)
            ).fetchone()
        except Exception:  # noqa: BLE001 — read-only проверка не должна ронять ingest
            return False
        return bool(row) and row[0] == content_hash

    # ---- поиск ----------------------------------------------------------------
    def search(self, q: str, *, project: str | None = None, limit: int = 10,
               sensitivity_allow: Iterable[str] | None = None) -> list[SearchHit]:
        """Поиск через HybridRetriever c ОБЯЗАТЕЛЬНЫМ sensitivity allow-list.

        allow-list всегда непустой (по умолчанию — несекретные уровни), поэтому
        чувствительный чанк не возвращается вызывающему без права. Настоящий сбой
        конвейера → errors.SearchFailed; сбой реранкера деградирует (SafeReranker).
        """
        allow = tuple(sensitivity_allow) if sensitivity_allow is not None else self.default_allow
        try:
            hits = self._engine.retriever.search(
                q, project=(project or ""), result_limit=limit, sensitivity_allow=allow)
        except errors.BossmanError:
            raise
        except Exception as exc:  # noqa: BLE001 — настоящий сбой плана поиска
            raise errors.SearchFailed(f"search pipeline failed: {type(exc).__name__}") from exc
        return [to_search_hit(h) for h in hits][:limit]

    @staticmethod
    def fuse(*ranked: list[SearchHit], limit: int = 10) -> list[SearchHit]:
        """RRF-слияние нескольких ранжированных списков (совместимость формы).

        Слияние делает уже HybridRetriever (lexical+vector) внутри единого стора;
        этот статик оставлен как утилита над результатами, БЕЗ второго индекса.
        """
        scores: dict[str, float] = {}
        hits: dict[str, SearchHit] = {}
        for result in ranked:
            for rank, h in enumerate(result, 1):
                key = h.document.id
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
                hits[key] = h
        ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [SearchHit(hits[k].document, v, ("rrf",)) for k, v in ordered]

    def close(self) -> None:
        """Закрыть стор только если движок наш (borrowed shared engine не трогаем)."""
        if self._owns:
            try:
                self._engine.close()
            except Exception:  # noqa: BLE001
                pass
