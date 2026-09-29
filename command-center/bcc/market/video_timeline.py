"""Local video timeline helpers for YouTube trading-learning ingestion.

Generic extraction policy only: source acquisition/transcription/scene detection
remain replaceable adapters. Trusted market numbers still require the calibrated
market vision path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Iterable


TRADING_CUES = (
    "cvd", "open interest", " oi ", "dpoc", "dvah", "dval", "dopen",
    "reclaim", "retest", "liquidation", "absorption", "volume",
)
VISUAL_CUES = (
    "look here", "look at", "as you can see", "on the chart", "this level",
    "here you can see", "watch this",
)


@dataclass(frozen=True)
class TimelineCandidate:
    t: float
    kind: str
    priority: int
    scene_id: str | None = None
    transcript: str = ""


def cue_priority(text: str) -> int:
    t = f" {(text or '').lower()} "
    score = 0
    if any(cue in t for cue in TRADING_CUES):
        score += 3
    if any(cue in t for cue in VISUAL_CUES):
        score += 2
    return score


def build_candidates(*, duration: float, scene_times: Iterable[float],
                     transcript_segments: Iterable[dict], periodic: float = 15.0) -> list[TimelineCandidate]:
    """Merge periodic, scene and transcript-cue timestamps deterministically."""
    by_bucket: dict[int, TimelineCandidate] = {}

    def add(item: TimelineCandidate):
        bucket = round(item.t * 2)  # 0.5s collision bucket
        old = by_bucket.get(bucket)
        if old is None or item.priority > old.priority:
            by_bucket[bucket] = item

    add(TimelineCandidate(0.0, "endpoint", 100))
    if duration > 0:
        add(TimelineCandidate(duration, "endpoint", 100))
    t = periodic
    while periodic > 0 and t < duration:
        add(TimelineCandidate(t, "periodic", 10))
        t += periodic
    for i, t in enumerate(scene_times):
        if 0 <= t <= duration:
            add(TimelineCandidate(float(t), "scene", 50, scene_id=f"s{i:04d}"))
    for seg in transcript_segments:
        text = str(seg.get("text") or "")
        p = cue_priority(text)
        if p:
            t = float(seg.get("start", 0))
            if 0 <= t <= duration:
                add(TimelineCandidate(t, "cue", 60 + p, transcript=text))
    return sorted(by_bucket.values(), key=lambda x: x.t)


def thin(candidates: list[TimelineCandidate], max_frames: int) -> list[TimelineCandidate]:
    """Keep high-value candidates first, while always preserving temporal endpoints."""
    if max_frames <= 0:
        return []
    if len(candidates) <= max_frames:
        return candidates
    keep = sorted(candidates, key=lambda x: (-x.priority, x.t))[:max_frames]
    return sorted(keep, key=lambda x: x.t)


def hamming_hex(a: str, b: str) -> int:
    """Hamming distance for equal-length perceptual hashes encoded as hex."""
    if len(a) != len(b):
        raise ValueError("hash length mismatch")
    return (int(a, 16) ^ int(b, 16)).bit_count()


def dedupe_phash(items: list[tuple[TimelineCandidate, str]], threshold: int = 6):
    """Compare with last KEPT frame, preserving high-priority cue/endpoint frames."""
    kept: list[tuple[TimelineCandidate, str]] = []
    for item, phash in items:
        if not kept or item.priority >= 60 or hamming_hex(kept[-1][1], phash) > threshold:
            kept.append((item, phash))
    return kept
