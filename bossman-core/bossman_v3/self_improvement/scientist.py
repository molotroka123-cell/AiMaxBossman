"""Scientific self-improvement decision layer for Bossman 1.5.

Hypothesis -> experiment -> evidence -> verifier -> promote/reject.
The learner never writes stable directly; this module only decides whether an
experiment is promotable into the next gated stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .lab import BenchmarkResult, dominates


Metric = Literal["verified_success", "quality", "cost", "latency", "tokens", "peak_ram",
                 "peak_vram", "retries"]


@dataclass(frozen=True)
class Hypothesis:
    id: str
    statement: str
    primary_metric: Metric
    min_relative_gain: float
    benchmark_ref: str
    risk: str = "low"


@dataclass(frozen=True)
class ExperimentEvidence:
    baseline: BenchmarkResult
    candidate: BenchmarkResult
    verifier_passed: bool
    regression_passed: bool
    unseen_transfer_passed: bool
    security_non_regression: bool
    source_sha: str
    candidate_ref: str
    complexity_cost: float = 0.0


@dataclass(frozen=True)
class ScientificDecision:
    promote: bool
    relative_gain: float
    pareto_improvement: bool
    reasons: tuple[str, ...]


class ScientificSelfImprovement:
    LOWER_BETTER = {"cost", "latency", "tokens", "peak_ram", "peak_vram", "retries"}

    @classmethod
    def relative_gain(cls, metric: Metric, baseline: BenchmarkResult,
                      candidate: BenchmarkResult) -> float:
        b = float(getattr(baseline, metric))
        c = float(getattr(candidate, metric))
        if b == 0:
            if metric in cls.LOWER_BETTER:
                return 0.0 if c == 0 else -1.0
            return 1.0 if c > 0 else 0.0
        return (b - c) / b if metric in cls.LOWER_BETTER else (c - b) / b

    @staticmethod
    def propose_from_observation(*, hypothesis_id: str, observation: dict,
                                 benchmark_ref: str) -> Hypothesis:
        """Create a bounded measurable hypothesis from a verified pain signal."""
        if observation.get("latency_regression"):
            metric, gain = "latency", float(observation.get("target_gain") or 0.20)
            statement = "Reduce verified task latency without lowering quality."
        elif observation.get("cost_regression"):
            metric, gain = "cost", float(observation.get("target_gain") or 0.20)
            statement = "Reduce verified task cost without lowering quality."
        elif observation.get("quality_regression"):
            metric, gain = "quality", float(observation.get("target_gain") or 0.02)
            statement = "Increase verified task quality without security regression."
        else:
            metric, gain = "verified_success", float(observation.get("target_gain") or 0.02)
            statement = "Increase verified success rate on the measured task class."
        if not 0 < gain <= 1:
            raise ValueError("target_gain")
        return Hypothesis(hypothesis_id, statement, metric, gain, benchmark_ref)

    def decide(self, hypothesis: Hypothesis, evidence: ExperimentEvidence) -> ScientificDecision:
        reasons = []
        if len(evidence.source_sha) != 40:
            reasons.append("invalid source sha")
        if not evidence.candidate_ref:
            reasons.append("missing candidate ref")
        if not hypothesis.benchmark_ref:
            reasons.append("missing benchmark")
        if not evidence.verifier_passed:
            reasons.append("independent verifier failed")
        if not evidence.regression_passed:
            reasons.append("regression suite failed")
        if not evidence.unseen_transfer_passed:
            reasons.append("unseen transfer failed")
        if not evidence.security_non_regression or evidence.candidate.security_failures > evidence.baseline.security_failures:
            reasons.append("security regression")
        if evidence.complexity_cost < 0:
            reasons.append("invalid complexity cost")
        gain = self.relative_gain(hypothesis.primary_metric, evidence.baseline, evidence.candidate)
        if gain + 1e-12 < hypothesis.min_relative_gain:
            reasons.append("hypothesis target not met")
        pareto = dominates(evidence.candidate, evidence.baseline)
        if not pareto:
            reasons.append("candidate is not Pareto-better")
        return ScientificDecision(not reasons, gain, pareto, tuple(reasons))
