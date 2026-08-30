"""
promotion_gate.py — Self-Learning Orchestrator Layer 5
Validation harness + gate controller.
Candidates pass only when benchmark + holdout confirm improvement without regression.
"""
from __future__ import annotations
import time
import uuid
import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from .candidate_generator import ImprovementCandidate, GATE_POLICY

try:
    from ..db import get_db
except ImportError:
    get_db = None


@dataclass
class ValidationReport:
    report_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id: str = ""
    benchmark_score: float = 0.0
    holdout_score: float = 0.0
    baseline_score: float = 0.0
    safety_pass: bool = False
    cost_ok: bool = False
    latency_ok: bool = False
    promoted: bool = False
    rejection_reason: str = ""
    ts: float = field(default_factory=time.time)


RunFn = Callable[[dict], dict]  # (config) -> {score, cost_usd, latency_ms, safety_flags}


class PromotionGate:
    """
    Validates a candidate against benchmark + holdout tasks.
    Promotes only if:
      - holdout_score > baseline_score + MIN_DELTA
      - benchmark_score >= BENCHMARK_FLOOR
      - safety_pass = True
      - cost and latency within limits

    Usage:
        gate = PromotionGate(run_fn=my_eval_fn)
        report = gate.validate(candidate, baseline_config, candidate_config)
    """

    MIN_DELTA = 0.03          # minimum improvement over baseline
    BENCHMARK_FLOOR = 0.65   # must not regress below this
    MAX_COST_REGRESSION = 0.20  # allow max 20% cost increase

    def __init__(
        self,
        run_fn: RunFn | None = None,
        benchmark_tasks: list[dict] | None = None,
        holdout_tasks: list[dict] | None = None,
    ):
        self.run_fn = run_fn or self._noop_run
        self.benchmark_tasks = benchmark_tasks or []
        self.holdout_tasks = holdout_tasks or []

    def validate(
        self,
        candidate: ImprovementCandidate,
        baseline_config: dict,
        candidate_config: dict,
    ) -> ValidationReport:
        report = ValidationReport(candidate_id=candidate.candidate_id)

        # Skip validation for DENY-gate candidates (they become tickets)
        if candidate.gate == "DENY":
            report.rejection_reason = "DENY gate — requires human dev ticket"
            report.promoted = False
            self._persist(report)
            return report

        # Run benchmark (fixed tasks, check for regression)
        b_results = self._run_tasks(self.benchmark_tasks, candidate_config)
        report.benchmark_score = self._avg_score(b_results)

        # Run holdout (unseen tasks, check for improvement)
        h_baseline = self._run_tasks(self.holdout_tasks, baseline_config)
        h_candidate = self._run_tasks(self.holdout_tasks, candidate_config)
        report.baseline_score = self._avg_score(h_baseline)
        report.holdout_score = self._avg_score(h_candidate)

        # Safety check
        safety_flags = [r.get("safety_flags", []) for r in b_results + h_candidate]
        report.safety_pass = all(len(f) == 0 for f in safety_flags)

        # Cost check
        baseline_cost = sum(r.get("cost_usd", 0) for r in h_baseline)
        candidate_cost = sum(r.get("cost_usd", 0) for r in h_candidate)
        if baseline_cost > 0:
            cost_increase = (candidate_cost - baseline_cost) / baseline_cost
            report.cost_ok = cost_increase <= self.MAX_COST_REGRESSION
        else:
            report.cost_ok = True

        # Latency check
        baseline_lat = sum(r.get("latency_ms", 0) for r in h_baseline)
        candidate_lat = sum(r.get("latency_ms", 0) for r in h_candidate)
        report.latency_ok = candidate_lat <= baseline_lat * 1.3 or baseline_lat == 0

        # Promotion decision
        delta = report.holdout_score - report.baseline_score
        if (
            delta >= self.MIN_DELTA
            and report.benchmark_score >= self.BENCHMARK_FLOOR
            and report.safety_pass
            and report.cost_ok
            and report.latency_ok
        ):
            if candidate.gate == "AUTO":
                report.promoted = True
            else:  # ASK
                report.promoted = False
                report.rejection_reason = f"gate=ASK: delta={delta:.3f} — awaiting human approval"
        else:
            report.promoted = False
            reasons = []
            if delta < self.MIN_DELTA:
                reasons.append(f"delta {delta:.3f} < {self.MIN_DELTA}")
            if report.benchmark_score < self.BENCHMARK_FLOOR:
                reasons.append(f"benchmark {report.benchmark_score:.2f} < floor")
            if not report.safety_pass:
                reasons.append("safety flags found")
            if not report.cost_ok:
                reasons.append("cost regression")
            if not report.latency_ok:
                reasons.append("latency regression")
            report.rejection_reason = "; ".join(reasons)

        self._persist(report)
        return report

    # ──────────────────────────────────────────── helpers ──

    def _run_tasks(self, tasks: list[dict], config: dict) -> list[dict]:
        if not tasks:
            return []
        results = []
        for task in tasks:
            try:
                result = self.run_fn({**task, "config": config})
                results.append(result)
            except Exception as exc:
                results.append({"score": 0.0, "error": str(exc)})
        return results

    @staticmethod
    def _avg_score(results: list[dict]) -> float:
        if not results:
            return 0.0
        return sum(r.get("score", 0.0) for r in results) / len(results)

    @staticmethod
    def _noop_run(task: dict) -> dict:
        """Placeholder run_fn — returns neutral scores. Replace with real harness."""
        return {"score": 0.5, "cost_usd": 0.001, "latency_ms": 500, "safety_flags": []}

    def _persist(self, report: ValidationReport) -> None:
        if get_db is None:
            return
        try:
            db = get_db()
            db.execute(
                """
                INSERT INTO validation_reports (
                    report_id, candidate_id, benchmark_score, holdout_score,
                    baseline_score, safety_pass, cost_ok, latency_ok,
                    promoted, rejection_reason, ts
                ) VALUES (
                    :report_id, :candidate_id, :benchmark_score, :holdout_score,
                    :baseline_score, :safety_pass, :cost_ok, :latency_ok,
                    :promoted, :rejection_reason, :ts
                )
                """,
                report.__dict__,
            )
            db.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("PromotionGate persist failed: %s", exc)
