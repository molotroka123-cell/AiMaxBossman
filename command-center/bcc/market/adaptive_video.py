"""Adaptive segment selection for video learning.

The selector is deliberately simple/deterministic first. A learned local scorer
may replace/augment it only after full-watch recall benchmarks exist.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

HIGH_CUES = {
    "entry": 5, "entered": 5, "long": 3, "short": 3, "stop": 5, "breakeven": 5,
    "break even": 5, "cvd": 4, "open interest": 4, "liquidation": 4,
    "dpoc": 4, "dvah": 4, "dval": 4, "reclaim": 4, "retest": 4,
    "sweep": 4, "absorption": 4, "invalidation": 5,
}
LOW_CUES = {"nft": -3, "giveaway": -3, "merch": -2, "subscribe": -1}


@dataclass(frozen=True)
class SegmentScore:
    score: float
    tier: str
    reasons: tuple[str, ...]
    uncertain: bool


def score_text(text: str) -> SegmentScore:
    t = " ".join((text or "").lower().split())
    points = 0
    reasons = []
    for cue, weight in HIGH_CUES.items():
        if re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", t):
            points += weight
            reasons.append(cue)
    for cue, weight in LOW_CUES.items():
        if cue in t:
            points += weight
            reasons.append(cue)
    # Map an interpretable heuristic into [0,1]. It is a prioritizer, not truth.
    score = max(0.0, min(1.0, 0.25 + points / 20))
    tier = "HIGH" if score >= .7 else "MEDIUM" if score >= .4 else "LOW"
    uncertain = .32 <= score <= .68
    return SegmentScore(round(score, 3), tier, tuple(reasons), uncertain)


def frame_budget(*, tier: str, duration_s: float, uncertain: bool = False) -> int:
    """Uncertainty increases inspection. No hard 30% quota."""
    per_min = {"HIGH": 10, "MEDIUM": 4, "LOW": 1}[tier]
    if uncertain:
        per_min = max(per_min, 6)
    return max(2, round(duration_s / 60 * per_min))


def audit_indices(total_low_segments: int, *, every: int = 10) -> list[int]:
    """Deterministic skipped-segment audit; reproducible instead of random."""
    if every <= 0:
        raise ValueError("every must be positive")
    return list(range(every - 1, total_low_segments, every))
