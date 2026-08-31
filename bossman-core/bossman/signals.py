"""V2.6 — DecisionSignals: малое типизированное общее состояние (раздел 23).

НЕ второй оркестратор и НЕ контроллер: один frozen-объект с нормированными
сигналами, который существующие контроллеры (router, reasoning, context, cost,
verifier) ЧИТАЮТ, чтобы не спорить друг с другом. Вычисление детерминированное
(никаких LLM-вызовов — правило «не звать модель, чтобы выбрать уровень
compute»), стоимость — микросекунды, fast path не трогается.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# Маркеры многошаговости/сложности (RU+EN) — сигнал, не приговор.
_MULTI_STEP = re.compile(
    r"(затем|потом|после (этого|чего)|снач|шаг|этап|and then|after that|step \d|"
    r"сравн|проанализ|исследу|research|собери|создай .*(отчёт|таблиц|презентац)|"
    r"refactor|мигрир|migrate)", re.I)
# Маркеры необратимости/риска (деньги, отправка, удаление, продакшен).
_RISK = re.compile(
    r"(оплат|плат[её]ж|перевод|купи|purchase|pay|отправ|send|email|письмо|"
    r"удали|delete|drop |rm -|деплой|deploy|prod|продакшен|push --force|reset --hard|"
    r"secret|секрет|парол|password|token|ключ)", re.I)


@dataclass(frozen=True, slots=True)
class DecisionSignals:
    """Все поля в [0,1]; 0.5 = нейтрально/неизвестно, если не сказано иное."""
    task_complexity: float = 0.0     # 0 = тривиально, 1 = многошаговый проект
    uncertainty: float = 0.0         # из uncertainty engine (0 = хватает evidence)
    risk: float = 0.0                # необратимость/безопасность/деньги
    evidence_confidence: float = 1.0 # 1 = наблюдения свежие и согласованные
    estimated_value: float = 0.5     # ценность исхода для владельца
    resource_budget: float = 1.0     # доля оставшегося бюджета (токены/деньги/время)
    latency_priority: float = 0.5    # 1 = ответ нужен немедленно
    reasons: tuple[str, ...] = field(default=())

    def with_(self, **kw) -> "DecisionSignals":
        """Обновлённая копия (frozen): контроллеры не мутируют чужое состояние."""
        return replace(self, **kw)


def _clamp(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def derive_signals(task_text: str, *,
                   previous_failures: int = 0,
                   tool_count: int = 0,
                   budget_left_fraction: float = 1.0) -> DecisionSignals:
    """Детерминированная первичная оценка по тексту задачи и известным фактам.

    Это стартовая точка: uncertainty engine и verifier уточняют сигналы по ходу
    (через `with_`). Никакой магии — прозрачные правила с reasons.
    """
    text = (task_text or "").strip()
    reasons: list[str] = []

    complexity = 0.0
    if len(text) > 400:
        complexity += 0.3
        reasons.append("длинная постановка (>400 символов)")
    multi = len(_MULTI_STEP.findall(text))
    if multi:
        complexity += min(0.5, 0.15 * multi)
        reasons.append(f"маркеры многошаговости ×{multi}")
    if tool_count >= 3:
        complexity += 0.2
        reasons.append(f"инструментов затребовано: {tool_count}")
    if previous_failures:
        complexity += min(0.3, 0.1 * previous_failures)
        reasons.append(f"прошлых провалов: {previous_failures}")

    risk = 0.0
    risky = len(_RISK.findall(text))
    if risky:
        risk = min(1.0, 0.3 + 0.15 * risky)
        reasons.append(f"маркеры необратимости/чувствительности ×{risky}")

    return DecisionSignals(
        task_complexity=_clamp(complexity),
        risk=_clamp(risk),
        resource_budget=_clamp(budget_left_fraction),
        reasons=tuple(reasons),
    )
