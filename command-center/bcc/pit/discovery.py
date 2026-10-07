from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .behavior_scores import discovery_threshold_adjustment

_NUMERIC_FIELDS = (
    "relevance",
    "uncertainty",
    "future_utility",
    "annoyance_cost",
    "sensitivity_risk",
)


def _is_finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _is_selectable(candidate: DiscoveryCandidate) -> bool:
    """Any non-finite numeric field is unknown risk, never a safe zero."""
    return all(
        _is_finite_number(getattr(candidate, field, None))
        for field in _NUMERIC_FIELDS
    )


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
    # Check finiteness first: a None/NaN sensitivity_risk must neither crash
    # the comparison nor slip past the suppression check below.
    if not _is_selectable(candidate):
        return float("-inf")
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
    risk_score: int = 0,
    engagement_score: int = 50,
) -> DiscoveryCandidate | None:
    """Return at most one low-friction question after a substantive answer.

    COLLECTION_FIRST deliberately lowers the threshold so the first experiment
    learns faster. It still never returns a suppressed/high-risk candidate.
    """
    if not enabled or mode == DiscoveryMode.OFF or not candidates:
        return None
    threshold = min(min_score, 0.0) if mode == DiscoveryMode.COLLECTION_FIRST else min_score
    # A local-only probing score may make benign discovery a little more eager
    # in collection-first mode. It never changes the one-question limit and
    # never permits a high-sensitivity candidate.
    if mode == DiscoveryMode.COLLECTION_FIRST and risk_score > 0:
        threshold -= min(int(risk_score), 5) * 0.02
    threshold += discovery_threshold_adjustment(engagement_score)
    best = max(candidates, key=score)
    return best if score(best) >= threshold else None
