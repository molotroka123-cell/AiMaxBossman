"""V2.6 — Uncertainty Engine (модуль A): детерминированная оценка СИСТЕМНОЙ
неопределённости.

Отвечает не на «уверенно ли звучит модель», а на «достаточно ли у СИСТЕМЫ
evidence, чтобы действовать»: разрыв в evidence, противоречия, провалы
verifier'а, устаревшие наблюдения, ненадёжность инструмента, риск, история
провалов. Самооценка модели НЕ авторитетна: она может только ПОДНЯТЬ
неопределённость (низкая уверенность — сигнал), но никогда не опустить её
(высокая уверенность — не доказательство). Чистые функции, без LLM/сети;
потребители (compute budget, router, verifier, research) читают сигнал
независимо — это НЕ оркестратор.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

# Веса компонент (сумма = 1.0). Порядок — как в формуле раздела 3 V2.6.
W_EVIDENCE_GAP = 0.25
W_CONTRADICTION = 0.20
W_VERIFIER_FAILURE = 0.20
W_STALENESS = 0.10
W_TOOL_UNCERTAINTY = 0.10
W_RISK = 0.10
W_FAILURE_HISTORY = 0.05


@dataclass(frozen=True, slots=True)
class UncertaintySignal:
    score: float                      # [0,1]; 0 = evidence достаточно
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    task_class: str = ""
    timestamp: float = field(default_factory=_time.time)


def _clamp(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def estimate(*, evidence_gap: float = 0.0, contradiction: float = 0.0,
             verifier_failure: float = 0.0, staleness: float = 0.0,
             tool_uncertainty: float = 0.0, risk: float = 0.0,
             failure_history: float = 0.0, task_class: str = "",
             evidence_refs: tuple[str, ...] = ()) -> UncertaintySignal:
    """U = Σ wᵢ·componentᵢ, каждый вход зажат в [0,1]. Прозрачно: каждый
    ненулевой вклад попадает в reasons."""
    parts = (
        ("evidence_gap", _clamp(evidence_gap), W_EVIDENCE_GAP),
        ("contradiction", _clamp(contradiction), W_CONTRADICTION),
        ("verifier_failure", _clamp(verifier_failure), W_VERIFIER_FAILURE),
        ("staleness", _clamp(staleness), W_STALENESS),
        ("tool_uncertainty", _clamp(tool_uncertainty), W_TOOL_UNCERTAINTY),
        ("risk", _clamp(risk), W_RISK),
        ("failure_history", _clamp(failure_history), W_FAILURE_HISTORY),
    )
    score = _clamp(sum(v * w for _, v, w in parts))
    reasons = tuple(f"{name}={v:.2f} (w={w:.2f})" for name, v, w in parts if v > 0.0)
    return UncertaintySignal(score=score, reasons=reasons,
                             evidence_refs=tuple(evidence_refs), task_class=task_class)


def apply_model_confidence(signal: UncertaintySignal,
                           model_confidence: float) -> UncertaintySignal:
    """Самооценка модели — НЕ доказательство (раздел 3 V2.6, req «manipulation»).

    Низкая уверенность модели (< 0.5) добавляет неопределённости; высокая
    уверенность НИКОГДА не снижает системный score — иначе модель могла бы
    «отговорить» систему от верификации, просто звуча уверенно.
    """
    conf = _clamp(model_confidence)
    if conf >= 0.5:
        return signal  # уверенность не является evidence — score не трогаем
    bump = (0.5 - conf) * 0.2  # максимум +0.1 при conf=0
    return UncertaintySignal(
        score=_clamp(signal.score + bump),
        reasons=signal.reasons + (f"model self-confidence low ({conf:.2f}) — "
                                  f"added +{bump:.2f}, high confidence never subtracts",),
        evidence_refs=signal.evidence_refs, task_class=signal.task_class)


async def failure_history_for_task(task_id: str) -> float:
    """[0,1] по числу НЕРАЗРЕШЁННЫХ провалов этой задачи в канонической
    failure memory — первый production-ЧИТАТЕЛЬ write-only таблицы failures.
    Ошибка БД не роняет вызывающего: неизвестно = 0.0."""
    try:
        from . import failure_memory
        unresolved = await failure_memory.get_unresolved_failures(task_id)
        return _clamp(len(unresolved) / 3.0)   # 3+ провала = максимум компоненты
    except Exception:  # noqa: BLE001 — сигнал вторичен
        return 0.0
