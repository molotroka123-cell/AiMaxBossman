from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol

from .embeddings import Embedder, cosine, valid_vector
from .models import RetrievalHit
from .store import ContextStore


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]: ...


class LexicalReranker:
    """Cheap deterministic reranker; production can inject a local cross encoder."""
    def rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        q = {x.lower() for x in re.findall(r"\w{2,}", query)}
        for h in hits:
            words = {x.lower() for x in re.findall(r"\w{2,}", h.chunk.text)}
            heading = {x.lower() for x in re.findall(r"\w{2,}", h.chunk.heading)}
            overlap = len(q & words) / max(1, len(q))
            heading_bonus = len(q & heading) / max(1, len(q))
            h.rerank_score = min(1.0, overlap * 0.8 + heading_bonus * 0.2)
        return sorted(hits, key=lambda x: x.rerank_score, reverse=True)


def _recency(updated: str) -> float:
    if not updated: return 0.5
    try:
        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if not dt.tzinfo: dt = dt.replace(tzinfo=timezone.utc)
        days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds()/86400)
        return math.exp(-days / 365.0)
    except Exception:
        return 0.5


class HybridRetriever:
    def __init__(self, store: ContextStore, embedder: Embedder, reranker: Reranker | None = None) -> None:
        self.store=store; self.embedder=embedder; self.reranker=reranker or LexicalReranker()

    def search(self, query: str, *, project: str = "", candidate_limit: int = 80, result_limit: int = 12,
               sensitivity_allow: tuple[str, ...] | None = None) -> list[RetrievalHit]:
        # Sensitivity-aware retrieval: агент без соответствующего permission не
        # получает sensitive chunk, даже если он релевантнее. None = без фильтра.
        allow = set(sensitivity_allow) if sensitivity_allow is not None else None
        def _ok(chunk) -> bool:
            return chunk.project == project and (allow is None or (chunk.sensitivity or "normal") in allow)
        by_id: dict[str, RetrievalHit] = {}
        for chunk, score in self.store.lexical_search(query, candidate_limit, project):
            if not _ok(chunk): continue
            by_id[chunk.chunk_id] = RetrievalHit(chunk=chunk, lexical_score=score, reasons=["lexical"])
        try:
            qv = self.embedder.embed([query])[0]
        except Exception:
            qv = []  # Local embedding failure must not erase lexical evidence.
        for chunk, vec in self.store.all_vector_chunks(project) if valid_vector(qv, self.embedder.dimension) else []:
            if not _ok(chunk): continue
            score = cosine(qv, vec)
            if score <= 0: continue
            hit = by_id.setdefault(chunk.chunk_id, RetrievalHit(chunk=chunk))
            hit.vector_score = max(hit.vector_score, score)
            hit.reasons.append("vector")
        candidates = sorted(by_id.values(), key=lambda h: max(h.lexical_score,h.vector_score), reverse=True)[:candidate_limit]
        candidates = self.reranker.rerank(query, candidates)
        for h in candidates:
            h.recency_score = _recency(h.chunk.updated_at)
            h.importance_score = h.chunk.importance
            h.final_score = (
                0.30*h.lexical_score + 0.32*h.vector_score + 0.24*h.rerank_score +
                0.08*h.recency_score + 0.06*h.importance_score
            )
        # Deduplicate exact content hashes while retaining the best source.
        best: dict[str, RetrievalHit] = {}
        for h in sorted(candidates,key=lambda x:x.final_score,reverse=True):
            key = h.chunk.content_hash or h.chunk.chunk_id
            best.setdefault(key,h)
        return list(best.values())[:result_limit]
