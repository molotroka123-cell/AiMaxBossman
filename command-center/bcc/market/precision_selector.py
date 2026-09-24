"""Precision-oriented multi-expert selector for K1M6A video segments.

Cheap signals nominate temporal arms. The selector deliberately preserves
uncertainty/exploration; it is not allowed to enforce a fixed 30% quota.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Mapping


@dataclass(frozen=True)
class ExpertScores:
    language: float = 0.0
    visual_change: float = 0.0
    numeric: float = 0.0
    structure: float = 0.0
    novelty: float = 0.0

    def values(self):
        return (self.language, self.visual_change, self.numeric, self.structure, self.novelty)


@dataclass(frozen=True)
class ArmDecision:
    priority: float
    uncertainty: float
    tier: str
    inspect: bool
    reasons: tuple[str, ...]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def fuse(scores: ExpertScores, *, samples: int = 1, boundary_bonus: float = 0.0,
         exploration_strength: float = .18) -> ArmDecision:
    """Union-preserving fusion: one strong expert cannot be averaged away."""
    vals = tuple(_clamp(v) for v in scores.values())
    mean = sum(vals) / len(vals)
    strongest = max(vals)
    disagreement = sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    uncertainty = _clamp(disagreement + exploration_strength / sqrt(max(1, samples)))
    # Strongest expert protects rare visual-only/numeric-only moments; mean rewards consensus.
    priority = _clamp(.55 * strongest + .30 * mean + .15 * uncertainty + boundary_bonus)
    tier = "HIGH" if priority >= .68 else "MEDIUM" if priority >= .40 else "LOW"
    inspect = tier != "LOW" or uncertainty >= .28
    names = ("language", "visual", "numeric", "structure", "novelty")
    reasons = tuple(n for n, v in zip(names, vals) if v >= .65)
    if uncertainty >= .28:
        reasons += ("uncertain_explore",)
    return ArmDecision(round(priority, 4), round(uncertainty, 4), tier, inspect, reasons)


def allocate(decisions: Mapping[str, ArmDecision], budget: int,
             *, uncertainty_share: float = .20, audit_share: float = .10) -> list[str]:
    """Allocate exploit/uncertainty/audit slots without a fixed coverage percentage."""
    if budget <= 0 or not decisions:
        return []
    ids = list(decisions)
    n_audit = min(len(ids), max(1, round(budget * audit_share)))
    n_uncertain = min(len(ids), round(budget * uncertainty_share))
    n_exploit = max(0, budget - n_audit - n_uncertain)

    chosen: list[str] = []
    def take(order, n):
        for k in order:
            if k not in chosen:
                chosen.append(k)
                if sum(1 for _ in chosen) >= target[0]:
                    break

    exploit = sorted(ids, key=lambda k: (-decisions[k].priority, k))
    uncertain = sorted(ids, key=lambda k: (-decisions[k].uncertainty, k))
    low = sorted(ids, key=lambda k: (decisions[k].priority, k))

    # Explicit loops keep each bucket size auditable.
    for order, n in ((exploit, n_exploit), (uncertain, n_uncertain), (low, n_audit)):
        added = 0
        for k in order:
            if k not in chosen:
                chosen.append(k); added += 1
                if added >= n:
                    break
    # Fill unused budget by best remaining priority.
    for k in exploit:
        if len(chosen) >= min(budget, len(ids)):
            break
        if k not in chosen:
            chosen.append(k)
    return chosen


def boundary_windows(t: float, duration: float) -> dict[str, tuple[float, float]]:
    def w(a, b):
        return (max(0.0, a), min(duration, b))
    return {
        "coarse_pre": w(t - 120, t - 30),
        "dense_pre": w(t - 30, t),
        "trigger": w(t - 10, t + 10),
        "management": w(t, t + 120),
    }
