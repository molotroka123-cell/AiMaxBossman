from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from .memsearch_bridge import MemoryHit

@dataclass(slots=True)
class ContextItem:
    source: str
    heading: str
    content: str
    score: float
    chunk_hash: str = ""

@dataclass(slots=True)
class ContextPack:
    query: str
    items: list[ContextItem]
    estimated_tokens: int
    text: str

def estimate_tokens(text: str) -> int:
    # Conservative language-agnostic estimate. Exact tokenizer is model-specific.
    return max(1, math.ceil(len(text) / 3.5))

def _fingerprint(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text[:500]

def build_context_pack(
    query: str,
    hits: Iterable[MemoryHit],
    *,
    max_tokens: int = 7000,
    max_items: int = 8,
    per_item_tokens: int = 1400,
) -> ContextPack:
    items: list[ContextItem] = []
    seen: set[str] = set()
    used = 0

    for h in hits:
        if len(items) >= max_items:
            break
        fp = _fingerprint(h.content)
        if not fp or fp in seen:
            continue
        seen.add(fp)

        max_chars = int(per_item_tokens * 3.5)
        content = h.content.strip()[:max_chars]
        citation = f"{h.source}" + (f" — {h.heading}" if h.heading else "")
        block = f"[SOURCE: {citation}]\n{content}"
        cost = estimate_tokens(block)
        if used + cost > max_tokens:
            continue
        used += cost
        items.append(ContextItem(
            source=h.source,
            heading=h.heading,
            content=content,
            score=h.score,
            chunk_hash=h.chunk_hash,
        ))

    text = "\n\n---\n\n".join(
        f"[SOURCE {i+1}: {x.source}{' — ' + x.heading if x.heading else ''}]\n{x.content}"
        for i, x in enumerate(items)
    )
    return ContextPack(query, items, estimate_tokens(text), text)
