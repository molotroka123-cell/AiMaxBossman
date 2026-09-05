"""Organizational Learning Loop (§14) — учёт наблюдаемых исходов.

Хранится ТОЛЬКО компактная операционная статистика по паре (агент, способность):
попытки, подтверждённые успехи, провалы, попытки ложного успеха, ретраи,
эскалации, стоимость, задержка. Никаких рассуждений модели, никаких транскриптов.

Оценка надёжности — байесовская с забыванием: Beta(1,1)-апостериор по
экспоненциально затухающим счётчикам, чтобы старые провалы не держали агента
вечно внизу, а новые — быстро проявлялись. Апостериор консервативен: у агента
без истории надёжность 0.5, а не 1.0 — «неизвестно» не равно «надёжно».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

DECAY = 0.9          # множитель старой истории при каждом новом наблюдении


@dataclass
class OutcomeStats:
    attempts: float = 0.0
    verified_success: float = 0.0
    failures: float = 0.0
    false_success_attempts: float = 0.0    # исполнитель заявил успех без подтверждения
    retries: float = 0.0
    escalations: float = 0.0
    cost_usd_total: float = 0.0
    latency_ms_total: float = 0.0
    last_outcome: str = ""

    # --------------------------------------------------------------- math

    @property
    def reliability(self) -> float:
        """P(подтверждённый успех) — сглаженная Beta(1,1)."""
        return (1.0 + self.verified_success) / (2.0 + self.attempts)

    @property
    def false_success_rate(self) -> float:
        return self.false_success_attempts / self.attempts if self.attempts else 0.0

    @property
    def avg_cost_usd(self) -> float:
        return self.cost_usd_total / self.attempts if self.attempts else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.latency_ms_total / self.attempts if self.attempts else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"attempts": self.attempts, "verified_success": self.verified_success,
                "failures": self.failures, "false_success_attempts": self.false_success_attempts,
                "retries": self.retries, "escalations": self.escalations,
                "cost_usd_total": self.cost_usd_total, "latency_ms_total": self.latency_ms_total,
                "last_outcome": self.last_outcome, "reliability": round(self.reliability, 4),
                "false_success_rate": round(self.false_success_rate, 4)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OutcomeStats":
        return cls(**{k: raw.get(k, 0.0) for k in ("attempts", "verified_success", "failures",
                                                   "false_success_attempts", "retries", "escalations",
                                                   "cost_usd_total", "latency_ms_total")},
                   last_outcome=str(raw.get("last_outcome", "")))


class OrganizationalLearning:
    """In-memory агрегат + persist через store (если дан)."""

    def __init__(self, store=None) -> None:
        self._stats: dict[tuple[str, str], OutcomeStats] = {}
        self.store = store
        if store is not None:
            for agent_id, capability, payload in store.learning():
                self._stats[(agent_id, capability)] = OutcomeStats.from_dict(payload)

    def stats(self, agent_id: str, capability: str) -> OutcomeStats:
        return self._stats.get((agent_id, capability), OutcomeStats())

    def observe(self, agent_id: str, capability: str, *, verified: bool, claimed_success: bool,
                cost_usd: float = 0.0, latency_ms: float = 0.0, retry: bool = False,
                escalated: bool = False) -> OutcomeStats:
        s = self._stats.setdefault((agent_id, capability), OutcomeStats())
        for name in ("attempts", "verified_success", "failures", "false_success_attempts",
                     "retries", "escalations", "cost_usd_total", "latency_ms_total"):
            setattr(s, name, getattr(s, name) * DECAY)
        s.attempts += 1
        if verified:
            s.verified_success += 1
            s.last_outcome = "verified"
        else:
            s.failures += 1
            s.last_outcome = "failed"
            if claimed_success:
                s.false_success_attempts += 1
                s.last_outcome = "false_success"
        s.retries += 1 if retry else 0
        s.escalations += 1 if escalated else 0
        s.cost_usd_total += max(0.0, cost_usd)
        s.latency_ms_total += max(0.0, latency_ms)
        if self.store is not None:
            self.store.save_learning(agent_id, capability, s.to_dict())
        return s

    def report(self) -> list[dict[str, Any]]:
        return [{"agent_id": a, "capability": c, **s.to_dict()}
                for (a, c), s in sorted(self._stats.items())]

    def failing_agents(self, *, min_attempts: float = 2.0, threshold: float = 0.4) -> list[dict[str, Any]]:
        """Кто систематически не подтверждает работу — сигнал для CEO control plane."""
        return [r for r in self.report()
                if r["attempts"] >= min_attempts and r["reliability"] < threshold]
