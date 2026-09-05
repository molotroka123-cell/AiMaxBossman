"""Сценарные контракты стресс-бенчмарков (V3_ORG_FLEET_STRESS). Сценарий —
callable, который ГОНЯЕТ реальный рантайм и возвращает измеренные факты;
evaluator лишь судит. Ничего не ждёт и не имитирует."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

from .models import BenchmarkPolicy


@dataclass(slots=True)
class ScenarioResult:
    name: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


class OrgBenchmarkSuite:
    def __init__(self, policy: BenchmarkPolicy | None = None) -> None:
        self.policy = policy or BenchmarkPolicy()

    def compound_failure(self, scenario: Callable[[], dict]) -> ScenarioResult:
        r = scenario()
        children = list(r.get("children") or [])
        bad = [c for c in children if c.get("failed") or not c.get("verified", False)]
        passed = len(children) >= 5 and bool(bad) and not r.get("parent_completed", False)
        return ScenarioResult("compound_failure", passed, dict(r, failed_or_unverified=len(bad)),
                              "" if passed else "parent completed with a failed/unverified required child or <5 children")

    def cross_department_leak(self, probe: Callable[[], dict]) -> ScenarioResult:
        r = probe()
        passed = bool(r.get("blocked")) and not bool(r.get("leaked"))
        return ScenarioResult("cross_department_leak", passed, r, "" if passed else "private knowledge leaked")

    def long_horizon_resume(self, scenario: Callable[[], dict]) -> ScenarioResult:
        t0 = perf_counter()
        r = scenario()
        elapsed = perf_counter() - t0
        passed = (int(r.get("duplicate_side_effect_count", -1)) == 0 and bool(r.get("resumed"))
                  and int(r.get("total_steps", -1)) >= 10 and int(r.get("crash_after_step", -1)) == 7
                  and int(r.get("completed_steps", 0)) == int(r.get("total_steps", -1))
                  and list(r.get("replayed_steps") or []) == [])
        r = dict(r, measured_elapsed_s=round(elapsed, 6), resume_sla_target_s=self.policy.resume_sla_s,
                 resume_sla_claimable=False)          # SLA — только по измерению на интегрированном рантайме
        return ScenarioResult("long_horizon_resume", passed, r, "" if passed else "replay or incomplete resume")

    def fleet_topology_stress(self, scenario: Callable[[], dict]) -> ScenarioResult:
        r = scenario()
        passed = (bool(r.get("capability_respected")) and bool(r.get("memory_respected"))
                  and bool(r.get("privacy_respected")) and not bool(r.get("privacy_violation"))
                  and bool(r.get("no_manual_ip_routing", True)))
        return ScenarioResult("fleet_topology_stress", passed, r, "" if passed else "placement ignored a constraint")

    def token_value_metric(self, *, quality: float, reliability: float, cost: float) -> float | None:
        if cost <= 0:
            return None
        return round((quality * reliability) / cost, 6)
