"""Deduplication of chunks and claims: near-duplicate detection by word-shingle Jaccard on normalised text."""
from __future__ import annotations

from .transcript import norm_words


def _shingles(text: str, n: int = 3) -> set:
    w = norm_words(text)
    if len(w) <= n:
        return {tuple(w)} if w else set()
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def jaccard(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedupe_chunks(chunks, threshold: float = 0.8) -> list:
    """Keep the first of each near-duplicate group (repeated intro, re-uploaded segment)."""
    kept = []
    for ch in chunks:
        if any(jaccard(ch.text, k.text) >= threshold for k in kept):
            continue
        kept.append(ch)
    return kept


def dedupe_claims(claims, known=(), threshold: float = 0.8):
    """Dedupe against the new batch and already-stored claims (`known`). Returns (kept, dropped_count)."""
    pool = list(known)
    kept, dropped = [], 0
    for c in claims:
        if any(jaccard(c.statement, k.statement) >= threshold for k in pool):
            dropped += 1
            continue
        kept.append(c)
        pool.append(c)
    return kept, dropped
