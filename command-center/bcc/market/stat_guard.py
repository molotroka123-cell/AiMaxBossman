"""Statistical guardrails for trading-learning research.

Pure functions only. These helpers make overlapping labels, clustered samples
and sparse-regime uncertainty explicit instead of letting an LLM reason them
away.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import NormalDist
from typing import Iterable


@dataclass(frozen=True)
class TimedSample:
    sample_id: str
    group_id: str
    decision_ts: float
    label_end_ts: float


def purged_split(samples: list[TimedSample], split_ts: float, embargo_s: float = 0.0):
    """Chronological split with purge/embargo for overlapping future labels."""
    train, test, purged = [], [], []
    lo, hi = split_ts - embargo_s, split_ts + embargo_s
    for s in sorted(samples, key=lambda x: (x.decision_ts, x.sample_id)):
        if s.decision_ts >= hi:
            test.append(s)
        elif s.label_end_ts < lo:
            train.append(s)
        else:
            purged.append(s)
    return train, test, purged


def effective_sample_size(group_ids: Iterable[str]) -> int:
    """Conservative independence count: one unit per session/day/source group."""
    return len({g for g in group_ids if g})


def beta_posterior_interval(wins: int, losses: int, *, prior_alpha: float = 1.0,
                            prior_beta: float = 1.0, z: float = 1.96) -> tuple[float, float, float]:
    """Fast beta posterior mean + normal-approx credible band for display/gating.

    Exact quantiles can be added when scipy is available; this dependency-free
    version intentionally widens sparse estimates rather than trusting raw rate.
    """
    if min(wins, losses) < 0 or min(prior_alpha, prior_beta) <= 0:
        raise ValueError("invalid beta counts")
    a, b = prior_alpha + wins, prior_beta + losses
    mean = a / (a + b)
    var = a * b / ((a + b) ** 2 * (a + b + 1))
    margin = z * sqrt(var)
    return mean, max(0.0, mean - margin), min(1.0, mean + margin)


def expectancy_r(rs: Iterable[float], costs_r: float = 0.0) -> float:
    vals = list(rs)
    if not vals:
        raise ValueError("no trades")
    return sum(vals) / len(vals) - costs_r


def benjamini_hochberg(p_values: list[float], alpha: float = .05) -> list[bool]:
    """FDR control for many mined strategy hypotheses."""
    if not 0 < alpha < 1 or any(not 0 <= p <= 1 for p in p_values):
        raise ValueError("invalid p-values/alpha")
    m = len(p_values)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    k = -1
    for rank, i in enumerate(order, 1):
        if p_values[i] <= alpha * rank / m:
            k = rank
    accepted = [False] * m
    if k > 0:
        cutoff = p_values[order[k - 1]]
        accepted = [p <= cutoff for p in p_values]
    return accepted


def volatility_normalized_distance(price: float, level: float, atr: float) -> float:
    if atr <= 0:
        raise ValueError("ATR must be positive")
    return abs(price - level) / atr
