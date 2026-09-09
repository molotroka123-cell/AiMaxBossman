"""File Intelligence — управляемая способность Bossman поверх AI File Sorter.

Что это НЕ: не парсер файлов (он живёт в `bossman.file_intel` и занимается
структурным извлечением содержимого) и не новая система полномочий. Здесь —
один узкий маршрут: предложить перекладку файлов сайдкаром, показать её
владельцу, применить утверждённое и доказать результат наблюдением.

Флаг `file_intelligence_v1` по умолчанию ВЫКЛЮЧЕН (§31). Выключенная фича не
регистрирует фоновых петель, не запускает моделей и не открывает эндпоинтов —
её отсутствие не должно быть заметно ничему остальному, и наоборот: её падение
не имеет права уронить старт Command Center.
"""
from __future__ import annotations

import os

from .models import (AppliedEffect, BackendMode, BinaryStatus, Denied, JobState,
                     Operation, PlanEntry, Receipt, Refusal, ReviewEnvelope,
                     SourceIdentity, TERMINAL_STATES, UNDO_HEADLESS, UpstreamStatus,
                     VERSION_UNVERIFIED)

#: Имя флага (§31). Значение читается из окружения по конвенции Command Center.
FLAG = "file_intelligence_v1"
FLAG_ENV = "BCC_FILE_INTELLIGENCE"

#: §7 — классы задач, которые владелец видит в интерфейсе. ВСЕ они
#: разворачиваются в одну и ту же операцию сайдкара и один и тот же
#: управляемый маршрут: отдельный путь исполнения на каждую кнопку — это
#: отдельная дыра на каждую кнопку.
TASK_CLASSES: dict[str, Operation] = {
    "file.rename_smart": Operation.RENAME,
    "file.categorize": Operation.CATEGORIZE,
    "file.categorize_and_rename": Operation.CATEGORIZE_AND_RENAME,
    "folder.organize": Operation.CATEGORIZE_AND_RENAME,
    "downloads.clean": Operation.CATEGORIZE,
    "photos.organize": Operation.CATEGORIZE,
    "documents.organize": Operation.CATEGORIZE,
    "media.organize": Operation.CATEGORIZE,
}


def enabled() -> bool:
    """Выключено по умолчанию — для заморозки и для продакшена.

    Владелец включает фичу явно на время теста. Умолчание «включено» означало
    бы, что способность двигать файлы появляется у системы сама.
    """
    return os.environ.get(FLAG_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def operation_for(task_class: str) -> Operation:
    """Класс задачи → операция сайдкара. Незнакомый класс — отказ, не догадка."""
    try:
        return TASK_CLASSES[task_class]
    except KeyError:
        raise Denied(Refusal.UNSUPPORTED_ARGUMENT,
                     f"{task_class!r} is not a File Intelligence task class",
                     known=sorted(TASK_CLASSES)) from None


__all__ = [
    "FLAG", "FLAG_ENV", "TASK_CLASSES", "UNDO_HEADLESS", "VERSION_UNVERIFIED",
    "AppliedEffect", "BackendMode", "BinaryStatus", "Denied", "JobState",
    "Operation", "PlanEntry", "Receipt", "Refusal", "ReviewEnvelope",
    "SourceIdentity", "TERMINAL_STATES", "UpstreamStatus",
    "enabled", "operation_for",
]
