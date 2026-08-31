"""Тонкий адаптер процесса: опциональный secret holdout + композитный гейт.

Точка, через которую promotion-потоки memory/skill/context/config обращаются к
Learning Quality Guard одним вызовом. Ничего не хранит durable (не второй Memory)
и по умолчанию — no-op (holdout не задан → ingest не режется, fast path не ломается).
"""
from __future__ import annotations

from .ab import ABVerdict, evaluate_ab
from .holdout import SecretHoldout
from .models import Candidate, SecuritySnapshot
from .promotion import advance

_HOLDOUT: SecretHoldout | None = None


def set_holdout(h: SecretHoldout | None) -> None:
    """Задать secret holdout на процесс (обычно при старте benchmark-эпохи)."""
    global _HOLDOUT
    _HOLDOUT = h


def get_holdout() -> SecretHoldout | None:
    return _HOLDOUT


def reject_if_holdout(task_id: str) -> None:
    """Колбэк для learning/memory/skills ingest. No-op, если holdout не задан."""
    h = _HOLDOUT
    if h is not None:
        h.reject_if_holdout(task_id)


def guard_promotion(candidate: Candidate, results, *,
                    security_before: SecuritySnapshot | None = None,
                    security_after: SecuritySnapshot | None = None,
                    shadow_runs: int = 0) -> tuple[Candidate, ABVerdict]:
    """Один вызов: A/B → анти-деградационные гейты → advance. Возвращает
    (обновлённый кандидат, вердикт A/B). OWNER_PROMOTED здесь не достигается —
    только через `promote(owner_approved=..., rollback=...)`.
    """
    verdict = evaluate_ab(results)
    moved = advance(candidate, ab=verdict, security_before=security_before,
                    security_after=security_after, shadow_runs=shadow_runs)
    return moved, verdict
