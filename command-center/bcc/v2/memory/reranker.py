from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .memsearch_bridge import MemoryHit

class RerankerUnavailable(RuntimeError):
    pass

@dataclass
class LocalCrossEncoderReranker:
    model_name: str = "BAAI/bge-reranker-v2-m3"
    _model: object | None = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:
            raise RerankerUnavailable(
                "Install sentence-transformers to enable local cross-encoder reranking"
            ) from exc
        self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, hits: Sequence[MemoryHit], *, top_k: int = 6) -> list[MemoryHit]:
        if not hits:
            return []
        model = self._load()
        pairs = [(query, h.content) for h in hits]
        scores = model.predict(pairs, show_progress_bar=False)
        ranked = sorted(zip(scores, hits), key=lambda x: float(x[0]), reverse=True)
        out: list[MemoryHit] = []
        for score, hit in ranked[:top_k]:
            hit.score = float(score)
            out.append(hit)
        return out
