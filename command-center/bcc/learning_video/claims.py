"""Claims with a verbatim cited quote. Model output is untrusted: every claim is re-validated deterministically."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .transcript import Chunk, norm_keep_len, norm_words

KINDS = ("definition", "procedure", "condition_rule", "numeric_fact", "opinion", "warning")
MIN_QUOTE_WORDS = 4
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{9,}\d")
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9]{32,}|0x[0-9a-fA-F]{40})\b")
_INJECTION = re.compile(r"(ignore (all |any )?(previous|prior) instructions|disregard .{0,20}instructions|"
                        r"игнорируй (все )?(предыдущие|прошлые) инструкции|забудь (все )?инструкции|system prompt)",
                        re.I)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def redact_pii(text: str):
    flags = []
    for name, rx in (("EMAIL", _EMAIL), ("PHONE", _PHONE), ("SECRET_LIKE", _SECRET)):
        if rx.search(text):
            flags.append(name)
            text = rx.sub("[%s]" % name, text)
    return text, flags


def _numbers(text: str) -> set:
    return {n.replace(",", ".") for n in _NUM.findall(text)}


@dataclass(frozen=True)
class Claim:
    claim_id: str
    statement: str
    quote: str
    kind: str
    source_id: str
    chunk_id: str
    t_start: float
    t_end: float
    source_kind: str
    lang: str
    status: str = "UNVERIFIED"

    def to_dict(self) -> dict:
        return asdict(self)


def locate_quote(chunk: Chunk, quote: str):
    """Find the quote in the chunk (case/punctuation/yo-insensitive). Returns (t_start, t_end) of the cues the quote
    spans, or None."""
    words = norm_words(quote)
    if len(words) < MIN_QUOTE_WORDS:
        return None
    rx = re.compile(r"(?<!\w)" + r"\W+".join(re.escape(w) for w in words) + r"(?!\w)")
    spans, parts, pos = [], [], 0
    for cue in chunk.cues:
        n = norm_keep_len(cue.text)
        spans.append((pos, pos + len(n), cue))
        parts.append(n)
        pos += len(n) + 1
    m = rx.search(" ".join(parts))
    if not m:
        return None
    hit = [c for a, b, c in spans if a < m.end() and b > m.start()]
    return (hit[0].start, hit[-1].end) if hit else None


def validate_claim(raw: dict, chunk: Chunk, *, source_kind: str, lang: str):
    """Return (Claim, None) or (None, reason). Reasons are stable strings for tests and reports."""
    stmt = str(raw.get("statement") or "").strip()
    quote = str(raw.get("quote") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    if not stmt or not quote:
        return None, "EMPTY"
    if kind not in KINDS:
        return None, "BAD_KIND"
    if _INJECTION.search(stmt) or _INJECTION.search(quote):
        return None, "INJECTION"
    if _EMAIL.search(quote) or _PHONE.search(quote) or _SECRET.search(quote):
        return None, "PII_IN_QUOTE"
    if redact_pii(stmt)[1]:
        return None, "PII_IN_STATEMENT"
    span = locate_quote(chunk, quote)
    if span is None:
        return None, "QUOTE_NOT_VERBATIM"
    if _numbers(stmt) - _numbers(quote):
        return None, "NUMBER_NOT_IN_QUOTE"
    cid = hashlib.sha1(("%s|%.2f|%s" % (chunk.source_id, span[0], stmt)).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return Claim(cid, stmt, quote, kind, chunk.source_id, chunk.chunk_id, span[0], span[1], source_kind, lang), None


def claims_from_model_json(text: str, chunk: Chunk, *, source_kind: str, lang: str):
    """Parse a model reply (JSON array, possibly fenced). Returns (claims, rejects[(reason, statement)])."""
    m = re.search(r"\[.*\]", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        return [], [("BAD_JSON", "")]
    ok, bad = [], []
    for item in data:
        if not isinstance(item, dict):
            bad.append(("BAD_ITEM", ""))
            continue
        c, why = validate_claim(item, chunk, source_kind=source_kind, lang=lang)
        if c:
            ok.append(c)
        else:
            bad.append((why, str(item.get("statement", ""))[:80]))
    return ok, bad


_RULES = (
    ("condition_rule", re.compile(r"\b(если|когда|if|when)\b.+\b(то|тогда|значит|then)\b", re.I)),
    ("definition", re.compile(r"\b(это|называется|означает|is called|means)\b", re.I)),
    ("procedure", re.compile(r"\b(сначала|затем|потом|шаг|first|then|next)\b", re.I)),
    ("warning", re.compile(r"\b(нельзя|опасно|ошибка|never|avoid)\b", re.I)),
    ("numeric_fact", re.compile(r"\d")),
)


def rule_extractor(chunk: Chunk, *, source_kind: str, lang: str) -> list:
    """Deterministic offline fallback: whole sentences matching a rule become claims quoting themselves. Weak but
    reproducible; the production extractor is a local model whose output goes through the same validate_claim."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", chunk.text):
        if len(norm_words(sent)) < MIN_QUOTE_WORDS + 1:
            continue
        for kind, rx in _RULES:
            if rx.search(sent):
                c, _ = validate_claim({"statement": sent.strip(), "quote": sent.strip(), "kind": kind}, chunk,
                                      source_kind=source_kind, lang=lang)
                if c:
                    out.append(c)
                break
    return out


def assert_loopback(url: str) -> str:
    """Privacy gate for extractor endpoints: transcripts only go to a model on this PC."""
    host = (urlparse(url).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("non-local endpoint refused: %r" % (host or url))
    return url
