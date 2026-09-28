"""Deterministic, no-LLM fact mining over a rendered page's visible text.

Reuses two public helpers from ``bcc/html_text.py`` — ``tokenize`` (same
lower-case word splitting used across the product's citation pipeline) and
``page_sha256`` (same hash convention as ``bcc/features/web_research``) —
so a page hash or a token computed here means the same thing everywhere else
in Bossman.

Deliberately NOT using ``html_text.extract``/``select_passages``: those parse
raw HTML into blocks, and the browser runtime's ``snapshot()`` only ever
exposes rendered ``innerText`` (secrets already redacted, captcha already
checked) — never raw HTML — to keep the collector on the same read-only,
secret-safe surface every other browser tool uses. This module rebuilds the
much smaller "sentence with an exact offset" idea directly on that text.

No model call anywhere in this file: a page becomes candidate facts by
keyword overlap and a small library of unit patterns a careful human skims
for (bandwidth, TDP, core counts, clock speeds, capacities). This keeps the
collector working even when Claude (or any cloud model) is unavailable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..html_text import page_sha256, tokenize  # noqa: F401 -- page_sha256 re-exported for callers
from . import config

#: A unit vocabulary a human researcher would recognise as "a fact", used to
#: raise confidence and to guess a predicate label. Not exhaustive by design
#: — it only needs to catch obviously-a-number-with-a-unit sentences; plain
#: keyword overlap still finds everything else.
_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bandwidth", re.compile(r"\b\d[\d.,]*\s*(GB/s|MB/s|Gbps|GT/s)\b", re.I)),
    ("power", re.compile(r"\b\d[\d.,]*\s*(W|watts?)\b", re.I)),
    ("cores", re.compile(r"\b\d+\s*(cores?|threads?)\b", re.I)),
    ("clock", re.compile(r"\b\d[\d.,]*\s*(GHz|MHz)\b", re.I)),
    ("capacity", re.compile(r"\b\d[\d.,]*\s*(GB|TB|MB)\b", re.I)),
    ("process_node", re.compile(r"\b\d+\s*nm\b", re.I)),
)

#: Personal contact details. A sentence carrying one is dropped whole, never
#: redacted: a redacted quote would no longer be the page's exact text (the
#: provenance promise), and a careful human researcher does not copy people's
#: e-mail addresses or phone numbers into their notes in the first place.
#: Deliberately narrow so spec numbers ("256 GB/s", "5.1 GHz", "2024-2026")
#: never match.
_PERSONAL_DATA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                     # e-mail
    re.compile(r"(?<![\w+])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\d)"),  # +CC ...
    re.compile(r"\(\d{2,4}\)\s?\d{3}[\s.-]?\d{3,4}(?!\d)"),                # (555) 123-4567
    re.compile(r"(?<!\d)\d{3}[.-]\d{3}[.-]\d{4}(?!\d)"),                     # 555-123-4567
)


def has_personal_data(sentence: str) -> bool:
    return any(p.search(sentence) for p in _PERSONAL_DATA_PATTERNS)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ0-9])|\n{1,}")


@dataclass(frozen=True, slots=True)
class Sentence:
    index: int
    offset: int
    text: str


def split_sentences(text: str) -> list[Sentence]:
    """Splits on sentence boundaries / newlines, keeping exact offsets into
    ``text`` so every later quote is independently verifiable:
    ``text[s.offset:s.offset + len(s.text)] == s.text`` always holds."""
    text = text or ""
    out: list[Sentence] = []
    pos = 0
    index = 0
    for piece in _SENTENCE_SPLIT_RE.split(text):
        if piece is None:
            continue
        start = text.index(piece, pos) if piece else pos
        stripped = piece.strip()
        if stripped:
            # Recompute the offset of the stripped text within the original.
            local = piece.index(stripped)
            offset = start + local
            index += 1
            out.append(Sentence(index=index, offset=offset, text=stripped))
        pos = start + len(piece)
    return out


def _guess_predicate(sentence: str, topic_tokens: set[str]) -> tuple[str, float]:
    """Best-effort predicate label + a confidence bump for matching a unit
    a human would recognise as "a specific fact" rather than prose."""
    for label, pattern in _UNIT_PATTERNS:
        if pattern.search(sentence):
            return label, 0.35
    return "mentions", 0.0


def score_sentence(sentence: str, topic_tokens: set[str]) -> float:
    """Jaccard-ish overlap between the sentence and the topic's tokens, in
    [0, 1]. Zero real overlap must stay at 0.0 — a positional "it's near the
    top of the page" prior would let an unrelated fact past the threshold."""
    words = set(tokenize(sentence))
    if not words or not topic_tokens:
        return 0.0
    overlap = words & topic_tokens
    if not overlap:
        return 0.0
    return len(overlap) / len(topic_tokens)


def candidate_facts(*, subject: str, text: str, topic: str, url: str,
                    retrieved_at_utc: str, robots_allowed: bool,
                    max_facts: int = config.MAX_FACTS_PER_PAGE,
                    min_score: float = config.MIN_FACT_SCORE) -> list[dict]:
    """Returns plain dicts (subject/predicate/value/confidence/quote/offset)
    ready for ``models.CollectedFact`` + ``models.Provenance``; kept as
    dicts here so this module has no dependency on ``models``/hashing
    choices beyond what it already reuses."""
    topic_tokens = set(tokenize(topic)) - _STOPWORDS_LOCAL
    sentences = split_sentences(text)
    scored: list[tuple[float, Sentence, str]] = []
    for s in sentences:
        if has_personal_data(s.text):
            continue                     # no personal-data harvesting (see _PERSONAL_DATA_PATTERNS)
        base = score_sentence(s.text, topic_tokens)
        if base <= 0.0:
            continue
        predicate, bump = _guess_predicate(s.text, topic_tokens)
        total = min(1.0, base + bump)
        if total < min_score:
            continue
        scored.append((total, s, predicate))
    scored.sort(key=lambda row: row[0], reverse=True)
    out = []
    for score, s, predicate in scored[:max_facts]:
        out.append({
            "subject": subject, "predicate": predicate, "value": s.text,
            "confidence": score, "quote": s.text, "selector": f"innerText#s{s.index}",
            "offset": s.offset,
        })
    return out


#: A tiny stopword set (not html_text's private `_STOPWORDS`, which is not
#: exported) so single-word topics like "the" don't dominate scoring.
_STOPWORDS_LOCAL = frozenset({
    "the", "a", "an", "of", "and", "or", "for", "in", "on", "with", "to", "is",
    "are", "how", "what", "much", "does",
})
