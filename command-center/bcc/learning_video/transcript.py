"""WEBVTT parsing (incl. YouTube rolling auto-captions), chunking with timestamps, source labels, quality heuristics."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SOURCE_KINDS = ("manual_subs", "auto_subs", "asr_local")
_TAG = re.compile(r"<[^>]*>")
_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_id: str
    start: float
    end: float
    text: str
    cues: tuple = field(default=(), compare=False)


def norm_keep_len(text: str) -> str:
    """Lower-case, yo->e, every non-alphanumeric char -> space. Length-preserving, so offsets map back to the raw text."""
    out = []
    for ch in text:
        if ch in "ёЁ":
            out.append("е")
        elif ch.isalnum():
            low = ch.lower()
            out.append(low if len(low) == 1 else ch)
        else:
            out.append(" ")
    return "".join(out)


def norm_words(text: str) -> list:
    return norm_keep_len(text).split()


def _secs(raw: str) -> float:
    m = _TS.fullmatch(raw.strip())
    if not m:
        raise ValueError("bad timestamp %r" % raw)
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def parse_vtt(text: str) -> list:
    """Parse WEBVTT. Inline tags are stripped; YouTube rolling captions (a cue repeating the previous cue's lines) keep
    only the lines not already seen in the previous cue. Malformed blocks are skipped, never guessed."""
    cues = []
    prev_lines = []
    for block in re.split(r"\r?\n\r?\n", text.replace("﻿", "")):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        try:
            a, b = lines[idx].split("-->", 1)
            start, end = _secs(a), _secs(b.split()[0])
        except (ValueError, IndexError):
            continue
        if end < start:
            continue
        body = []
        for ln in lines[idx + 1:]:
            clean = re.sub(r"\s+", " ", _TAG.sub("", ln)).strip()
            if clean:
                body.append(clean)
        fresh = [ln for ln in body if ln not in prev_lines]
        prev_lines = body or prev_lines
        if fresh:
            cues.append(Cue(start, end, " ".join(fresh)))
    return cues


def source_label(*, url: str, kind: str, lang: str, raw_bytes: bytes) -> dict:
    """Immutable passport of a transcript: where it came from and how. kind must be one of SOURCE_KINDS."""
    if kind not in SOURCE_KINDS:
        raise ValueError("unknown source kind %r" % kind)
    sha = hashlib.sha256(raw_bytes).hexdigest()
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url or "")
    vid = m.group(1) if m else ""
    return {"source_id": "%s-%s" % (vid or "local", sha[:10]), "video_id": vid, "url": url, "kind": kind,
            "lang": lang, "sha256": sha, "trust": "low" if kind == "auto_subs" else "normal"}


def chunk_cues(cues: list, source_id: str, *, max_chars: int = 700, min_chars: int = 120) -> list:
    """Greedy chunks on cue boundaries. A tiny tail is merged into the previous chunk."""
    groups, cur, size = [], [], 0
    for cue in cues:
        if cur and size + len(cue.text) + 1 > max_chars:
            groups.append(cur)
            cur, size = [], 0
        cur.append(cue)
        size += len(cue.text) + 1
    if cur:
        if groups and size < min_chars:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    out = []
    for g in groups:
        text = " ".join(c.text for c in g)
        cid = hashlib.sha1(("%s|%.2f|%s" % (source_id, g[0].start, text)).encode("utf-8")).hexdigest()[:16]
        out.append(Chunk(cid, source_id, g[0].start, g[-1].end, text, tuple(g)))
    return out


def transcript_quality(cues: list, lang: str = "ru") -> dict:
    """Cheap, explainable heuristics -> score 0..1 and flags. Not a WER; a gate that routes bad input to re-ASR."""
    flags = []
    text = " ".join(c.text for c in cues)
    words = norm_words(text)
    if len(words) < 30:
        flags.append("TOO_SHORT")
    if "�" in text or re.search(r"[ÐÑ][\u0080-¿]", text):
        flags.append("MOJIBAKE")
    letters = [c for c in text if c.isalpha()]
    if letters:
        cyr = sum(1 for c in letters if "а" <= c.lower() <= "я" or c in "ёЁ") / len(letters)
        if lang == "ru" and cyr < 0.6:
            flags.append("LANG_MISMATCH")
        if lang == "en" and cyr > 0.2:
            flags.append("LANG_MISMATCH")
    if len(words) >= 30:
        top = max(words.count(w) for w in set(words))
        if top / len(words) > 0.2:
            flags.append("REPEATED_TOKEN")
    if cues:
        span = max(c.end for c in cues) - min(c.start for c in cues)
        if span > 0 and len(words) / span > 7:
            flags.append("IMPOSSIBLE_RATE")
        if span > 0 and len(words) / span < 0.3:
            flags.append("SPARSE")
    severe = {"MOJIBAKE", "LANG_MISMATCH", "REPEATED_TOKEN", "IMPOSSIBLE_RATE", "TOO_SHORT"}
    score = max(0.0, 1.0 - sum(0.5 if f in severe else 0.3 for f in flags))
    return {"score": round(score, 2), "flags": flags, "words": len(words), "usable": score >= 0.7}
