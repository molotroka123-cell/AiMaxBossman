"""Secret Holdout (req.2): набор задач, НЕДОСТУПНЫЙ learning/memory/skills.

Хранятся только СОЛЁНЫЕ хеши id — нельзя перечислить holdout и «обучиться вокруг».
learning/memory/skills могут лишь СПРОСИТЬ `is_holdout()` / `reject_if_holdout()`,
но не получить список. Так holdout остаётся честным независимым срезом для
измерения деградации.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


class HoldoutViolation(PermissionError):
    """Задача из secret holdout попала в обучающий/памятный/skill-путь — запрещено."""


def _seal(task_id: str) -> str:
    return hashlib.sha256(("bossman-holdout:" + str(task_id)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SecretHoldout:
    """Запечатанный holdout. НЕТ метода перечисления — это намеренно (req.2)."""
    _sealed: frozenset

    @classmethod
    def seal(cls, task_ids) -> "SecretHoldout":
        return cls(frozenset(_seal(t) for t in task_ids))

    def is_holdout(self, task_id: str) -> bool:
        return _seal(task_id) in self._sealed

    def reject_if_holdout(self, task_id: str) -> None:
        """Вызывается на входе в learning/memory/skills. Holdout → отказ."""
        if self.is_holdout(task_id):
            raise HoldoutViolation(
                "secret holdout task must never enter learning/memory/skills")

    def filter_learnable(self, task_ids):
        """Оставить только НЕ-holdout id (для безопасного обучающего входа)."""
        return [t for t in task_ids if not self.is_holdout(t)]

    def __len__(self) -> int:
        return len(self._sealed)
