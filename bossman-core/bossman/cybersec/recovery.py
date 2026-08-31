"""Cyber Recovery Mode — реакция на подтверждённый инцидент.

Строится на существующем восстановлении Computer Operator / Recovery Kernel:
здесь только ПОРЯДОК шагов и правило «сохранить улики до отката». Ничего
не откатывает молча и никогда не повышает себе права.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class RecoveryStep(IntEnum):
    PRESERVE_EVIDENCE = 0   # улики фиксируются ПЕРВЫМИ (откат их уничтожит)
    CONTAIN = 1             # изолировать: отозвать аренду, остановить задачу
    INVALIDATE_STATE = 2    # пометить наблюдения/таргетинг устаревшими
    REVOKE_SESSIONS = 3     # отозвать сессии/токены, если затронуты
    ROLLBACK = 4            # откат к чекпоинту
    REVERIFY = 5            # свежее наблюдение + проверка инвариантов
    ASK_OWNER = 6           # эскалация владельцу


@dataclass(frozen=True)
class RecoveryPlan:
    steps: tuple[RecoveryStep, ...]
    reason: str
    requires_owner: bool = False


def plan(*, severity: str, contained: bool, state_tampered: bool = False,
         sessions_affected: bool = False) -> RecoveryPlan:
    """Собрать план восстановления. Улики — всегда первым шагом."""
    steps: list[RecoveryStep] = [RecoveryStep.PRESERVE_EVIDENCE]
    if not contained:
        steps.append(RecoveryStep.CONTAIN)
    steps.append(RecoveryStep.INVALIDATE_STATE)
    if sessions_affected:
        steps.append(RecoveryStep.REVOKE_SESSIONS)
    if state_tampered:
        steps.append(RecoveryStep.ROLLBACK)
    steps.append(RecoveryStep.REVERIFY)

    owner = severity in {"high", "critical"} or state_tampered or sessions_affected
    if owner:
        steps.append(RecoveryStep.ASK_OWNER)
    return RecoveryPlan(tuple(steps), f"severity={severity} contained={contained}", owner)


def evidence_before_rollback(p: RecoveryPlan) -> bool:
    """Инвариант: улики фиксируются строго ДО отката."""
    if RecoveryStep.ROLLBACK not in p.steps:
        return True
    return p.steps.index(RecoveryStep.PRESERVE_EVIDENCE) < p.steps.index(RecoveryStep.ROLLBACK)
