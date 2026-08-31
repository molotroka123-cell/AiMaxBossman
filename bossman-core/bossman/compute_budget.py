"""V2.6 — Adaptive Compute Budget (модуль B): C0–C4, EVR, VOI.

Лёгкие задачи не платят почти ничего сверх; тяжёлые получают больше compute
ТОЛЬКО при положительной ожидаемой ценности. Выбор уровня — детерминированные
пороги над DecisionSignals (LLM для выбора уровня НЕ вызывается — это правило
раздела 4 V2.6). Обязательная security/safety-верификация не скипается
никаким VOI.
"""
from __future__ import annotations

from enum import IntEnum

from .signals import DecisionSignals


class ComputeLevel(IntEnum):
    C0_FAST = 0             # прямой ответ, без retrieval/критиков
    C1_NORMAL = 1           # обычная петля
    C2_DEEP = 2             # + retrieval/verifier глубже
    C3_MULTI_CANDIDATE = 3  # + альтернативы/критик
    C4_MAX_VERIFICATION = 4 # максимум проверок (необратимое/безопасность)


# Операции, которые НЕЛЬЗЯ отменить по экономике (VOI<=0 не аргумент):
MANDATORY_ACTIONS = frozenset({"security_verification", "safety_verification",
                               "approval", "egress_guard", "ingest_guard"})


def select_level(signals: DecisionSignals) -> tuple[ComputeLevel, list[str]]:
    """Детерминированные пороги; каждый триггер объясняется. Микросекунды."""
    reasons: list[str] = []
    level = ComputeLevel.C1_NORMAL

    if signals.risk >= 0.6:
        level = ComputeLevel.C4_MAX_VERIFICATION
        reasons.append(f"risk {signals.risk:.2f} >= 0.6 -> C4 (необратимость)")
    elif signals.uncertainty >= 0.7:
        level = ComputeLevel.C3_MULTI_CANDIDATE
        reasons.append(f"uncertainty {signals.uncertainty:.2f} >= 0.7 -> C3")
    elif signals.task_complexity >= 0.6 or signals.uncertainty >= 0.4:
        level = ComputeLevel.C2_DEEP
        reasons.append("complexity/uncertainty среднее -> C2")
    elif (signals.task_complexity < 0.2 and signals.risk < 0.3
          and signals.uncertainty < 0.3):
        level = ComputeLevel.C0_FAST
        reasons.append("тривиально: complexity<0.2, risk<0.3, uncertainty<0.3 -> C0")
    else:
        reasons.append("базовый уровень C1")

    if signals.resource_budget < 0.1 and level > ComputeLevel.C1_NORMAL \
            and signals.risk < 0.6:
        level = ComputeLevel.C1_NORMAL
        reasons.append("бюджет почти исчерпан -> не выше C1 (кроме high-risk)")
    return level, reasons


def evr(p_improve: float, *, delta_quality: float = 0.0,
        delta_success: float = 0.0, delta_risk_reduction: float = 0.0,
        wq: float = 1.0, ws: float = 1.0, wr: float = 1.0,
        token_cost: float = 0.0, latency_cost: float = 0.0,
        memory_cost: float = 0.0, money_cost: float = 0.0) -> float:
    """EVR = P_improve·(wq·ΔQ + ws·ΔSuccess + wr·ΔRiskReduction) − Σ costs."""
    gain = p_improve * (wq * delta_quality + ws * delta_success
                        + wr * delta_risk_reduction)
    return gain - (token_cost + latency_cost + memory_cost + money_cost)


def should_continue_reasoning(evr_value: float, threshold: float = 0.0) -> bool:
    return evr_value > threshold


def voi(expected_uncertainty_after: float, uncertainty_now: float,
        cost: float) -> float:
    """VOI(action) = ожидаемое СНИЖЕНИЕ неопределённости − цена действия.
    (U — «плохая» величина, поэтому ценность = U_now − E[U_after].)"""
    return (uncertainty_now - expected_uncertainty_after) - cost


def may_skip(action: str, voi_value: float) -> bool:
    """VOI<=0 → опциональное действие можно пропустить, но обязательная
    security/safety-верификация не отменяется экономикой НИКОГДА."""
    if action in MANDATORY_ACTIONS:
        return False
    return voi_value <= 0.0
