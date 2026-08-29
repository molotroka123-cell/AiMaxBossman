"""Stage 10 — Dev Factory: модели задания и конечный автомат шагов.

Петля: задача → план → изолированная копия → код → тесты в песочнице →
состязательное ревью → правка → доказательства → патч → ОЖИДАНИЕ ВЛАДЕЛЬЦА.

Жёсткие границы, закодированные в типах:
- финальный результат — ПАТЧ, а не push/merge; публикация требует подтверждения;
- «успех» без доказательств невозможен: у шага есть Evidence, и терминальный
  DONE недостижим, пока тесты не отдали verdict PASS;
- бюджет попыток конечен и не восстанавливается сам;
- консеквентные шаги записываются в журнал, чтобы перезапуск их НЕ повторил.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobState(str, Enum):
    PLANNED = "PLANNED"            # план составлен, ничего не исполнено
    PREPARING = "PREPARING"        # готовится изолированная рабочая копия
    RUNNING = "RUNNING"            # идут шаги
    REVIEWING = "REVIEWING"        # независимое состязательное ревью
    NEEDS_FIX = "NEEDS_FIX"        # ревью/тесты вернули замечания, есть бюджет
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # патч готов, ждём владельца
    APPROVED = "APPROVED"          # владелец подтвердил (публикацию делает он)
    REJECTED = "REJECTED"          # владелец отклонил
    FAILED = "FAILED"              # бюджет исчерпан / неустранимая ошибка
    CANCELLED = "CANCELLED"        # отменено
    DONE = "DONE"                  # завершено с доказательствами


TERMINAL: frozenset[JobState] = frozenset({
    JobState.APPROVED, JobState.REJECTED, JobState.FAILED,
    JobState.CANCELLED, JobState.DONE,
})

_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PLANNED: frozenset({JobState.PREPARING, JobState.CANCELLED, JobState.FAILED}),
    JobState.PREPARING: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}),
    JobState.RUNNING: frozenset({JobState.REVIEWING, JobState.NEEDS_FIX,
                                 JobState.CANCELLED, JobState.FAILED}),
    JobState.REVIEWING: frozenset({JobState.AWAITING_APPROVAL, JobState.NEEDS_FIX,
                                   JobState.CANCELLED, JobState.FAILED}),
    JobState.NEEDS_FIX: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}),
    JobState.AWAITING_APPROVAL: frozenset({JobState.APPROVED, JobState.REJECTED,
                                           JobState.CANCELLED}),
    JobState.APPROVED: frozenset({JobState.DONE}),
    JobState.REJECTED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.DONE: frozenset(),
}


def can_transition(src: JobState, dst: JobState) -> bool:
    return dst in _TRANSITIONS.get(src, frozenset())


def allowed_transitions(src: JobState) -> frozenset[JobState]:
    return _TRANSITIONS.get(src, frozenset())


class StepKind(str, Enum):
    EDIT = "EDIT"          # правка кода в изолированной копии
    TEST = "TEST"          # прогон тестов в песочнице
    REVIEW = "REVIEW"      # независимое ревью
    PATCH = "PATCH"        # сборка патча (последний шаг перед подтверждением)


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"    # шаг не дал доказательств — считаем НЕ успехом


# Шаги, которые меняют что-то за пределами рабочей копии. Их выполнение
# записывается в журнал, чтобы перезапуск НЕ повторил их вслепую.
CONSEQUENTIAL_KINDS: frozenset[StepKind] = frozenset({StepKind.PATCH})


@dataclass(slots=True)
class Evidence:
    """Доказательство результата шага. Без него «успеха» не существует."""
    verdict: Verdict = Verdict.UNKNOWN
    summary: str = ""
    stdout_path: str | None = None
    artifacts: tuple[str, ...] = ()
    passed: int = 0
    failed: int = 0

    @property
    def proves_success(self) -> bool:
        return self.verdict is Verdict.PASS and self.failed == 0


@dataclass(slots=True)
class DevStep:
    id: str
    kind: StepKind
    description: str
    argv: tuple[str, ...] = ()
    evidence: Evidence = field(default_factory=Evidence)
    attempts: int = 0
    done: bool = False


@dataclass(slots=True)
class RetryBudget:
    """Конечный бюджет попыток. Сам не восстанавливается."""
    max_attempts: int = 3
    used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_attempts

    def spend(self) -> None:
        self.used += 1


@dataclass(slots=True)
class Patch:
    """Готовый к ревью человеком результат. Публикацию делает ВЛАДЕЛЕЦ."""
    diff: str
    files: tuple[str, ...] = ()
    sha256: str = ""
    evidence_summary: str = ""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass(slots=True)
class DevJob:
    id: str
    task: str
    repo_path: str
    steps: list[DevStep] = field(default_factory=list)
    state: JobState = JobState.PLANNED
    budget: RetryBudget = field(default_factory=RetryBudget)
    workspace: str | None = None
    patch: Patch | None = None
    approval_id: int | None = None
    trusted_repo: bool = False     # незнакомый репозиторий = НЕдоверенный
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[tuple[float, str, str]] = field(default_factory=list)
    # Журнал уже выполненных консеквентных шагов: перезапуск их не повторяет.
    performed: list[str] = field(default_factory=list)
    error: str | None = None

    def record(self, state: JobState, note: str = "") -> None:
        self.state = state
        self.updated_at = time.time()
        self.history.append((self.updated_at, state.value, note))

    def mark_performed(self, step_id: str) -> None:
        if step_id not in self.performed:
            self.performed.append(step_id)

    def already_performed(self, step_id: str) -> bool:
        return step_id in self.performed
