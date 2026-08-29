"""Stage 10 — Autonomous Development Factory.

Петля: Task → план → изолированная копия → код → тесты в песочнице →
состязательное ревью → правка → доказательства → ПАТЧ → ожидание владельца.

Переиспользует существующее и НЕ создаёт дублей: песочница (Этап 8), Resource
Brain (Этап 4), Gateway (Этап 3), Context/Memory (2.222), approvals, events,
общие швы errors/lifecycle/obs.

Фабрика НИКОГДА не пушит, не мержит и не меняет защищённые настройки: её
терминал — AWAITING_APPROVAL с готовым патчем.
"""
from __future__ import annotations

from .evidence import from_test_output, write_evidence
from .executor import SandboxExecutor
from .factory import DevFactory
from .models import (
    CONSEQUENTIAL_KINDS,
    DevJob,
    DevStep,
    Evidence,
    JobState,
    Patch,
    RetryBudget,
    StepKind,
    Verdict,
    allowed_transitions,
    can_transition,
)
from .planner import (
    ALLOWED_TEST_BINARIES,
    FakePlanner,
    LLMPlanner,
    Planner,
    detect_injection,
    wrap_untrusted,
)
from .reviewer import AdversarialReviewer, ReviewResult
from .workspace import WorkspaceManager

__all__ = [
    "DevFactory", "DevJob", "DevStep", "JobState", "StepKind", "Verdict", "Evidence",
    "Patch", "RetryBudget", "CONSEQUENTIAL_KINDS", "can_transition", "allowed_transitions",
    "FakePlanner", "LLMPlanner", "Planner", "detect_injection", "wrap_untrusted",
    "ALLOWED_TEST_BINARIES",
    "AdversarialReviewer", "ReviewResult", "WorkspaceManager", "SandboxExecutor",
    "from_test_output", "write_evidence", "build_subsystem", "router",
]


def build_subsystem():
    from .subsystem import DevFactorySubsystem
    return DevFactorySubsystem()


try:  # pragma: no cover
    from .routes import router
except Exception:  # noqa: BLE001
    router = None  # type: ignore[assignment]
