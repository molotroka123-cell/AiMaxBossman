"""Benchmark contract for selector v2 -> v3 promotion."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityComponents:
    pixel_perfect_recall: float
    entry_recall: float
    stop_be_recall: float
    loss_scratch_recall: float
    numeric_correctness: float
    level_correctness: float
    boundary_accuracy: float
    duplicate_suppression: float
    false_positive_control: float

    def score(self) -> float:
        weights = (.25, .15, .10, .10, .15, .10, .05, .05, .05)
        values = tuple(self.__dict__.values())
        if any(not 0 <= v <= 1 for v in values):
            raise ValueError("quality components must be within [0,1]")
        return sum(w * v for w, v in zip(weights, values))


@dataclass(frozen=True)
class Benchmark:
    wall_seconds: float
    quality: QualityComponents
    wrong_verified_numbers: int = 0


def promotion(v2: Benchmark, v3: Benchmark, *, speedup_required: float = 2.0,
              relative_quality_required: float = 1.10) -> dict:
    if min(v2.wall_seconds, v3.wall_seconds) <= 0:
        raise ValueError("wall time must be positive")
    speedup = v2.wall_seconds / v3.wall_seconds
    q2, q3 = v2.quality.score(), v3.quality.score()
    quality_ratio = q3 / q2 if q2 else float("inf")
    critical_numeric_gate = v3.wrong_verified_numbers == 0
    passed = speedup >= speedup_required and quality_ratio >= relative_quality_required and critical_numeric_gate
    return {
        "pass": passed,
        "speedup": speedup,
        "wall_reduction": 1 - v3.wall_seconds / v2.wall_seconds,
        "quality_v2": q2,
        "quality_v3": q3,
        "quality_ratio": quality_ratio,
        "critical_numeric_gate": critical_numeric_gate,
    }
