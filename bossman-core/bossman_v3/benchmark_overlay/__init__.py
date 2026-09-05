"""Пассивный benchmark overlay V3 (адаптация AiMaxBossman_Benchmark_Overlay_DropIn_v2).

РЕАЛЬНАЯ СИСТЕМА → durable-истина (org store, fleet flights, TaskJournal)
    → адаптеры (`adapters.py`, только чтение) → BenchmarkCollector → BenchmarkScorer
    → HardFailGate → BenchmarkReport → `scripts/update_readme_scorecard.py --from-benchmark`
    → `docs/benchmark/current-scorecard.json` → README-проекция.

Инварианты: overlay ничего не меняет в исполнении, политике, вердикте верификатора,
ретраях и маршрутизации; событие бенчмарка ≠ доказательство исполнения; агрегат
`total` — вторичен (10 осей независимы); при нулевой стоимости метрика «за доллар»
= None, а не деление на ноль. Live-SLA (1.5 с) не заявляется без измерения на
интегрированном рантайме.
"""
from .adapters import events_from_fleet, events_from_organization, events_from_task_journal
from .baseline import RegressionResult, compare_reports
from .collector import BenchmarkCollector
from .evaluator import OrgBenchmarkSuite, ScenarioResult
from .hard_fail import HARD_FAILS, HardFailGate
from .models import BenchmarkEvent, BenchmarkPolicy, BenchmarkReport, MissionScore
from .report import write_reports
from .scorer import DIMS, BenchmarkScorer

__all__ = [
    "BenchmarkEvent", "BenchmarkPolicy", "BenchmarkReport", "MissionScore", "BenchmarkCollector", "HardFailGate",
    "HARD_FAILS", "BenchmarkScorer", "DIMS", "OrgBenchmarkSuite", "ScenarioResult", "compare_reports",
    "RegressionResult", "write_reports", "events_from_organization", "events_from_fleet", "events_from_task_journal",
]
