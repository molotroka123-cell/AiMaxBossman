from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Токенизатор берём у владельца лексики — `local_index`. Раньше он приезжал из
# `chunking.py`, где лежала его побайтная копия: запрос переранжировался одним
# токенизатором, а индекс строился другим.
from .local_index import tokenize
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


@dataclass
class LexicalReranker:
    """Дешёвый переранжировщик без моделей: пересечение термов запроса +
    покрытие + бонус за совпадение в заголовке. Нужен, потому что
    cross-encoder (`LocalCrossEncoderReranker`) требует sentence-transformers,
    которых здесь нет. Работает всегда, деградации не требует.

    Веса — ПОЛЯ, а не константы в формуле (ragflow.md §5.1 п.3: у них
    `tkweight/vtweight` — параметры). Когда появится dense-путь, смешивание уже
    готово: `dense_weight` домножает `hit.metadata["dense_score"]`, если он
    есть, и равен нулю, пока dense не появился — поведение не меняется.
    """

    coverage_weight: float = 2.0
    head_hit_weight: float = 1.5
    score_weight: float = 0.05
    dense_weight: float = 0.0

    def weights(self) -> dict[str, float]:
        return {"coverage": self.coverage_weight,
                "head_hit": self.head_hit_weight,
                "score": self.score_weight,
                "dense": self.dense_weight}

    def _dense_score(self, hit) -> float:
        meta = getattr(hit, "metadata", None) or {}
        try:
            return float(meta.get("dense_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def rerank(self, query: str, hits, *, top_k: int = 8):
        q = set(tokenize(query))
        if not q:
            return list(hits)[:top_k]
        scored = []
        for h in hits:
            body = set(tokenize(h.content))
            head = set(tokenize(h.heading)) | set(tokenize(Path(h.source).stem))
            coverage = len(q & body) / len(q)
            head_hit = len(q & head) / len(q)
            total = (coverage * self.coverage_weight
                     + head_hit * self.head_hit_weight
                     + h.score * self.score_weight
                     + self._dense_score(h) * self.dense_weight)
            scored.append((total, h))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, hit in scored[:top_k]:
            hit.score = round(float(score), 4)
            out.append(hit)
        return out

    def rerank_dedup_decay(
        self,
        query: str,
        hits,
        *,
        top_k: int = 5,
        half_life_days: float = 30.0,
        jaccard_threshold: float = 0.80,
    ):
        """Rerank with temporal decay (`metadata.age_days`) and near-duplicate suppression."""
        import math
        base = self.rerank(query, hits, top_k=max(len(hits), top_k))
        decayed = []
        for h in base:
            meta = getattr(h, "metadata", None) or {}
            try:
                age_days = max(0.0, float(meta.get("age_days") or 0.0))
            except (TypeError, ValueError):
                age_days = 0.0
            decay = math.exp(-math.log(2.0) * age_days / max(0.1, half_life_days)) if age_days > 0 else 1.0
            adj_score = round(float(h.score) * decay, 4)
            decayed.append((adj_score, set(tokenize(h.content)), h))
        decayed.sort(key=lambda x: x[0], reverse=True)

        selected = []
        seen_sets: list[set[str]] = []
        for score, tok_set, hit in decayed:
            if tok_set and any(
                (len(tok_set & prev) / max(1, len(tok_set | prev))) >= jaccard_threshold
                for prev in seen_sets
            ):
                continue
            hit.score = score
            selected.append(hit)
            if tok_set:
                seen_sets.append(tok_set)
            if len(selected) >= top_k:
                break
        return selected

