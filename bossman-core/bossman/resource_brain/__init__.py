"""Resource Brain (Этап 4) — измерение единого пула и допуск нагрузки.

Публичный контракт (совместим с прототипом; на нём держатся приёмочные тесты):
`from bossman.resource_brain import *` выдаёт `ResourceSnapshot`,
`WorkloadRequest`, `AdmissionDecision`, `ResourceBrain`.

Пакет также экспонирует для api.py два атрибута-шва:
- `build_subsystem()` — фабрика подсистемы (lifecycle.Subsystem);
- `router` — read-only APIRouter (`/resource/...`).

Единый на процесс `BRAIN` — синглтон, который наполняет фоновый цикл пробы и
читают HTTP-эндпоинты. Снимок и реестр аренд ЭФЕМЕРНЫ (в памяти, восстанавливаются
пустыми на рестарте) — никакого нового durable-хранилища не заводим.
"""
from __future__ import annotations

from .brain import ResourceBrain
from .ledger import LeaseLedger
from .models import (
    AdmissionDecision,
    ModelResidency,
    PressureLevel,
    ResourceLease,
    ResourceSnapshot,
    WorkloadRequest,
)
from .probe import (
    AmdUnifiedProbe,
    CpuProbe,
    ProbeAdapter,
    detect_probe,
    snapshot,
)
from .subsystem import ResourceBrainSubsystem

# Синглтон процесса: общий для подсистемы (фоновый цикл) и роутера (чтение).
BRAIN = ResourceBrain()


def build_subsystem() -> ResourceBrainSubsystem:
    """Фабрика подсистемы для реестра жизненного цикла. Возвращает объект,
    удовлетворяющий протоколу lifecycle.Subsystem (name/critical/validate/start/
    stop), связанный с процессным синглтоном `BRAIN`."""
    return ResourceBrainSubsystem(BRAIN, detect_probe())


# Роутер импортируем терпимо: пакет обязан импортироваться даже без FastAPI
# (например, при импорте одного лишь ядра admission в минимальном окружении).
try:  # pragma: no cover - зависит от наличия fastapi
    from .routes import router
except Exception:  # noqa: BLE001
    router = None  # type: ignore[assignment]


# __all__ намеренно узкий: гарантирует, что `import *` даёт ровно контрактные
# имена (плюс полезные типы Этапа 4), не протаскивая внутренние модули.
__all__ = [
    "ResourceSnapshot",
    "WorkloadRequest",
    "AdmissionDecision",
    "ResourceBrain",
    "ResourceLease",
    "PressureLevel",
    "ModelResidency",
    "LeaseLedger",
    "ProbeAdapter",
    "CpuProbe",
    "AmdUnifiedProbe",
    "ResourceBrainSubsystem",
    "detect_probe",
    "snapshot",
    "build_subsystem",
    "router",
    "BRAIN",
]
