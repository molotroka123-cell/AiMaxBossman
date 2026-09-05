from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class BenchmarkEvent:
    """Наблюдение для бенчмарка. НЕ доказательство исполнения — производится
    адаптерами из durable-истины, а не сообщается исполнителем."""
    kind: str
    mission_id: str
    ts: float
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "adapter"          # adapter:<layer> | test


@dataclass(slots=True)
class BenchmarkPolicy:
    stale_evidence_max_age_s: float = 300.0
    critical_regression_threshold: float = 5.0
    resume_sla_s: float = 1.5        # заявляется только по измерению на интегрированном рантайме


@dataclass(slots=True)
class MissionScore:
    mission_id: str
    scores: dict[str, float | None]
    hard_failures: list[str]
    verified_success: bool
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        """Вторичный агрегат (0–100). Любой hard fail → 0. Не авторитетен: оси независимы."""
        if self.hard_failures:
            return 0.0
        vals = [v if v is not None else 0.0 for v in self.scores.values()]
        return round((sum(vals) / max(len(vals), 1)) * 10.0, 2)


@dataclass(slots=True)
class BenchmarkReport:
    benchmark_version: str
    git_sha: str
    mode: str                        # DETERMINISTIC | REAL_SANDBOX | LIVE
    mission_scores: list[MissionScore]
    aggregate: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for m, ms in zip(d["mission_scores"], self.mission_scores):
            m["total_secondary"] = ms.total
        return d
