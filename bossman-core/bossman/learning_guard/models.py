"""Типы Learning Quality Guard. Только данные — ни сети, ни моделей, ни хранилищ.

Тонкий слой поверх существующей архитектуры: НЕ второй Memory/Context/Router/
Policy/Verifier/EventBus. Guard лишь ПРИНИМАЕТ измерения (verified-успех A/B) и
ВЫДАЁТ вердикт/стадию. Персистентность — через существующую память вызывающего.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class PromotionStage(IntEnum):
    """candidate → validation → shadow → verified → owner (req.7)."""
    CANDIDATE = 0
    VALIDATION = 1
    SHADOW = 2
    VERIFIED = 3
    OWNER_PROMOTED = 4        # только по явному решению владельца, не автоматически


@dataclass(frozen=True)
class ABResult:
    """Одна задача, прогнанная ДВАЖДЫ ОДНОЙ И ТОЙ ЖЕ моделью (req.1):
    raw = модель напрямую; guarded = модель + Bossman.

    Доказательство — ТОЛЬКО `*_verified` (внешняя верификация). `bossman_self_score`
    хранится для аудита, но в гейтах НЕ используется (req.6: self-score ≠ evidence).
    """
    task_id: str
    task_class: str
    raw_verified: bool
    guarded_verified: bool
    raw_tokens: int = 0
    guarded_tokens: int = 0
    bossman_self_score: float | None = None      # audit-only, НЕ evidence


@dataclass(frozen=True)
class RollbackInfo:
    """Метаданные отката (req.10): куда вернуться, если promotion деградирует."""
    prev_stage: str
    prev_ref: str                 # id/hash предыдущей версии кандидата/конфига
    reason: str = ""


@dataclass(frozen=True)
class SecuritySnapshot:
    """Срез security-гейтов для проверки, что их не «оптимизировали» ради score.

    Конвенция: `leaks`/`bypasses` — больше ХУЖЕ; `containment_rate` — больше ЛУЧШЕ.
    """
    leaks: int = 0
    bypasses: int = 0
    containment_rate: float = 1.0


@dataclass(frozen=True)
class Candidate:
    """Кандидат на продвижение (memory/skill/context/config)."""
    kind: str                     # "memory" | "skill" | "context" | "config"
    ref: str
    stage: PromotionStage = PromotionStage.CANDIDATE
    reasons: tuple[str, ...] = ()
    rollback: RollbackInfo | None = None
