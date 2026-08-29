"""Stage 10 — контракт планировщика.

КРИТИЧНО: содержимое репозитория (README, issues, комментарии, веб) — это
НЕДОВЕРЕННЫЕ ДАННЫЕ, а не инструкции. Планировщик обязан их обрамлять и никогда
не позволять им менять политику, границы или бюджет. Здесь же — детерминированный
FakePlanner для E2E без модели.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import DevStep, StepKind, new_id

# Маркеры, которыми обрамляется любой внешний текст перед подачей модели.
UNTRUSTED_OPEN = "<<<UNTRUSTED_REPO_CONTENT>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_REPO_CONTENT>>>"

# Формулировки, которыми prompt-injection пытается снять ограничения. Наличие
# такого текста НЕ выполняется, а помечается для ревью.
INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "disregard the above",
    "you are now", "system:", "new instructions", "override policy",
    "disable approval", "skip approval", "auto-merge", "push directly",
    "игнорируй", "новые инструкции", "отключи подтверждение", "запушь",
)


def wrap_untrusted(text: str) -> str:
    """Обрамить внешний текст так, чтобы он читался как ДАННЫЕ."""
    safe = text.replace(UNTRUSTED_OPEN, "").replace(UNTRUSTED_CLOSE, "")
    return f"{UNTRUSTED_OPEN}\n{safe}\n{UNTRUSTED_CLOSE}"


def detect_injection(text: str) -> tuple[str, ...]:
    """Найденные попытки инъекции. Пустой кортеж — ничего подозрительного."""
    low = (text or "").lower()
    return tuple(m for m in INJECTION_MARKERS if m in low)


@runtime_checkable
class Planner(Protocol):
    def plan(self, task: str, repo_context: str) -> list[DevStep]:
        """Составить план шагов. repo_context — НЕДОВЕРЕННЫЙ текст."""


class FakePlanner:
    """Детерминированный планировщик для E2E без модели: правка → тесты →
    ревью → патч. Инъекции в repo_context НЕ меняют план (проверяется тестом)."""

    def __init__(self, test_argv: tuple[str, ...] = ("python3", "-m", "pytest", "-q")) -> None:
        self.test_argv = test_argv

    def plan(self, task: str, repo_context: str) -> list[DevStep]:
        _ = wrap_untrusted(repo_context)      # контент — данные, не инструкции
        return [
            DevStep(id=new_id("st"), kind=StepKind.EDIT, description=f"реализовать: {task}"),
            DevStep(id=new_id("st"), kind=StepKind.TEST, description="прогон тестов",
                    argv=self.test_argv),
            DevStep(id=new_id("st"), kind=StepKind.REVIEW, description="состязательное ревью"),
            DevStep(id=new_id("st"), kind=StepKind.PATCH, description="сборка патча"),
        ]
