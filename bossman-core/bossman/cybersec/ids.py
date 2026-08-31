"""Agent Behavior IDS — аномалии в ПОВЕДЕНИИ агента, а не в тексте.

Слушает то, что уже происходит в системе (отказы политики, запросы секретов,
попытки расширить скоуп, повторы, провалы верификации) и выдаёт оценку риска.
IDS ничего не запрещает сам — он повышает требуемый уровень подтверждения и
сигналит. Авторитет остаётся у Policy/Approval.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("low", "medium", "high", "critical")


@dataclass(frozen=True)
class BehaviorSignal:
    policy_denials: int = 0
    secret_requests: int = 0
    scope_escalations: int = 0
    repeated_actions: int = 0
    verifier_failures: int = 0
    injection_hits: int = 0
    sandbox_escape_attempts: int = 0


@dataclass(frozen=True)
class IDSResult:
    score: float
    severity: str
    reasons: tuple[str, ...] = ()
    recommend_containment: bool = False


# Веса: намеренные нарушения границы весят больше, чем шум исполнения.
_W = {
    "sandbox_escape_attempts": 0.50,
    "scope_escalations": 0.30,
    "secret_requests": 0.30,
    "injection_hits": 0.20,
    "policy_denials": 0.15,
    "verifier_failures": 0.10,
}


def score_behavior(s: BehaviorSignal) -> IDSResult:
    score = 0.0
    reasons: list[str] = []
    for field_name, weight in _W.items():
        n = int(getattr(s, field_name, 0) or 0)
        if n > 0:
            score += weight * n
            reasons.append(field_name)
    # Повторы — сигнал застревания/долбёжки, но только сверх нормального ретрая.
    extra = max(0, int(s.repeated_actions) - 2)
    if extra:
        score += 0.08 * extra
        reasons.append("loop_risk")

    score = min(1.0, round(score, 4))
    severity = ("critical" if score >= 0.8 else "high" if score >= 0.6
                else "medium" if score >= 0.3 else "low")
    # Любая попытка побега из песочницы — сразу containment, независимо от суммы.
    contain = severity in {"high", "critical"} or s.sandbox_escape_attempts > 0
    return IDSResult(score, severity, tuple(reasons), contain)
