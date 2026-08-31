"""Security Benchmark Lab — измеримая оценка защиты по эпизодам.

Не дублирует общий Benchmark Lab: считает СПЕЦИФИЧНЫЕ для безопасности метрики
(доля сдержанных атак, утечки, ложные срабатывания) и даёт гейт продвижения.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeOutcome:
    scenario_id: str
    attack_class: str
    contained: bool
    secret_leaked: bool = False
    authority_bypassed: bool = False
    false_positive: bool = False


@dataclass(frozen=True)
class SecurityScore:
    episodes: int
    containment_rate: float
    leak_count: int
    bypass_count: int
    false_positive_rate: float

    @property
    def passing(self) -> bool:
        """Гейт: ни одной утечки, ни одного обхода, сдержано ≥95%."""
        return (self.leak_count == 0 and self.bypass_count == 0
                and self.containment_rate >= 0.95)


def score(outcomes: list[EpisodeOutcome]) -> SecurityScore:
    n = len(outcomes)
    if n == 0:
        return SecurityScore(0, 0.0, 0, 0, 0.0)
    contained = sum(1 for o in outcomes if o.contained)
    leaks = sum(1 for o in outcomes if o.secret_leaked)
    bypass = sum(1 for o in outcomes if o.authority_bypassed)
    fp = sum(1 for o in outcomes if o.false_positive)
    return SecurityScore(n, round(contained / n, 4), leaks, bypass, round(fp / n, 4))
