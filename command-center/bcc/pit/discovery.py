from __future__ import annotations

from dataclasses import dataclass


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
) -> DiscoveryCandidate | None:
    """Return at most one optional personalization question."""
    if not enabled or not candidates:
        return None
    best = max(candidates, key=score)
    return best if score(best) >= min_score else None
