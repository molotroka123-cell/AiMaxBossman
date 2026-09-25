from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DiscoveryMode(StrEnum):
    OFF = "off"
    BALANCED = "balanced"
    COLLECTION_FIRST = "collection_first"


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    key: str
    question: str
    relevance: float
    uncertainty: float
    future_utility: float
    annoyance_cost: float = 0.2
    sensitivity_risk: float = 0.0
    previously_skipped: bool = False


def score(candidate: DiscoveryCandidate) -> float:
    if candidate.previously_skipped or candidate.sensitivity_risk >= 0.5:
        return float("-inf")
    return (
        candidate.relevance
        * candidate.uncertainty
        * candidate.future_utility
        - candidate.annoyance_cost
        - candidate.sensitivity_risk
    )


def choose_discovery_question(
    candidates: list[DiscoveryCandidate],
    *,
    enabled: bool,
    min_score: float = 0.08,
    mode: DiscoveryMode = DiscoveryMode.BALANCED,
) -> DiscoveryCandidate | None:
    """Return at most one low-friction question after a substantive answer.

    COLLECTION_FIRST deliberately lowers the threshold so the first experiment
    learns faster. It still never returns a suppressed/high-risk candidate.
    """
    if not enabled or mode == DiscoveryMode.OFF or not candidates:
        return None
    threshold = min(min_score, 0.0) if mode == DiscoveryMode.COLLECTION_FIRST else min_score
    best = max(candidates, key=score)
    return best if score(best) >= threshold else None
