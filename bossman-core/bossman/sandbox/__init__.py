"""Stage 8 — AI Lab Sandbox: изолированная среда исполнения для агентного кода.

Публичный контракт пакета. Инвариант «OFF значит OFF»: по умолчанию фича
ВЫКЛЮЧЕНА (BOSSMAN_SANDBOX_ENABLED не задан) — тогда менеджер ничего не
поднимает, а любой create() отдаёт SandboxDisabled.

Переиспользует существующие подсистемы, а не дублирует их:
- Resource Brain (Этап 4) — admission аренд (resources.ResourceLeaseAdapter);
- errors/lifecycle/correlation/obs (общие швы этапов 4–7);
- Gateway (Этап 3) и Context/Memory (Этап 2.222) вызываются через свои интерфейсы
  (второго Gateway/памяти НЕ заводим).
"""
from __future__ import annotations

import os

from .artifacts import ArtifactGate
from .dataset import CandidateState, DatasetCandidate, DatasetGate
from .egress import EgressProxy
from .manager import SandboxManager
from .models import (
    Artifact,
    IsolationTier,
    NetworkMode,
    PolicyMode,
    ResourceRequest,
    RiskAssessment,
    RiskLevel,
    RuntimeCapabilities,
    SandboxPolicy,
    SandboxSession,
    SandboxSpec,
    SandboxState,
    SecretGrant,
    allowed_transitions,
    can_transition,
)
from .network import NetworkGuard
from .policy import PolicyEngine, RiskEngine
from .resources import ResourceLeaseAdapter
from .runtime import FakeRuntime, SandboxRuntime
from .secrets import InMemorySecretBroker, PostgresSecretBroker
from .trajectory import TrajectoryRecorder

__all__ = [
    "SandboxManager", "SandboxSpec", "SandboxSession", "SandboxState", "SandboxPolicy",
    "PolicyMode", "NetworkMode", "RiskLevel", "IsolationTier", "RiskAssessment",
    "ResourceRequest", "RuntimeCapabilities", "SecretGrant", "Artifact",
    "PolicyEngine", "RiskEngine", "NetworkGuard", "ResourceLeaseAdapter",
    "SandboxRuntime", "FakeRuntime", "InMemorySecretBroker", "PostgresSecretBroker",
    "ArtifactGate",
    "TrajectoryRecorder", "DatasetGate", "DatasetCandidate", "CandidateState",
    "EgressProxy",
    "can_transition", "allowed_transitions",
    "sandbox_enabled", "build_subsystem", "router",
]


def sandbox_enabled() -> bool:
    """Фича включена только явным флагом. Дефолт — OFF (non-negotiable #1)."""
    return os.getenv("BOSSMAN_SANDBOX_ENABLED", "0").lower() in ("1", "true", "yes", "on")


def build_subsystem():
    """Фабрика подсистемы для реестра жизненного цикла (lifecycle.Subsystem)."""
    from .subsystem import SandboxSubsystem
    return SandboxSubsystem()


# Роутер импортируем терпимо (пакет обязан импортироваться без FastAPI).
try:  # pragma: no cover
    from .routes import router
except Exception:  # noqa: BLE001
    router = None  # type: ignore[assignment]
