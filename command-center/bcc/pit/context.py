from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ContextItem:
    id: str
    text: str
    score: float
    category: str


_WORD = re.compile(r"[\w-]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(str(text)) if len(m.group(0)) > 2}


def select_persona_context(
    query: str,
    records: Iterable[dict[str, Any]],
    *,
    max_items: int = 24,
) -> list[ContextItem]:
    """Cheap first-stage retrieval for laptop experiments.

    The raw vault is never dumped into a model prompt. This can later be
    replaced by the existing Bossman retrieval stack without changing storage
    or Telegram contracts.
    """
    q = _tokens(query)
    scored: list[ContextItem] = []
    for row in records:
        value = str(row.get("value", ""))
        key = str(row.get("key", ""))
        category = str(row.get("category", ""))
        overlap = len(q & (_tokens(value) | _tokens(key) | _tokens(category)))
        confidence = float(row.get("confidence", 0.0) or 0.0)
        utility = float(row.get("utility_score", 0.0) or 0.0)
        evidence = str(row.get("evidence_kind", ""))
        confirmed_bonus = 0.35 if evidence == "confirmed" else (0.15 if evidence == "explicit" else 0.0)
        score = overlap * 1.2 + confidence * 0.7 + utility * 0.5 + confirmed_bonus
        if overlap or score >= 0.8:
            scored.append(ContextItem(str(row.get("id", "")), value, score, category))
    scored.sort(key=lambda x: (x.score, x.id), reverse=True)
    return scored[:max_items]
