from __future__ import annotations

from dataclasses import dataclass

from .models import BenchmarkPolicy, BenchmarkReport


@dataclass(slots=True)
class RegressionResult:
    baseline_sha: str
    current_sha: str
    score_delta: float
    hard_fail_delta: int
    result: str                      # PASS | REGRESSION | CRITICAL


def compare_reports(baseline: BenchmarkReport, current: BenchmarkReport, policy: BenchmarkPolicy | None = None) -> RegressionResult:
    """Сравнение по вторичному агрегату и по числу hard fail'ов. Новый hard fail — всегда CRITICAL."""
    policy = policy or BenchmarkPolicy()
    b = float(baseline.aggregate.get("total_score_secondary", 0))
    c = float(current.aggregate.get("total_score_secondary", 0))
    delta = round(c - b, 2)
    hd = len(current.aggregate.get("hard_failures") or []) - len(baseline.aggregate.get("hard_failures") or [])
    result = "CRITICAL" if (hd > 0 or delta < -policy.critical_regression_threshold) else ("REGRESSION" if delta < 0 else "PASS")
    return RegressionResult(baseline.git_sha, current.git_sha, delta, hd, result)
