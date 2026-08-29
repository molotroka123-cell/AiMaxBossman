"""Stage 8 — подсистема жизненного цикла песочницы (lifecycle.Subsystem).

OFF значит OFF: выключенная фича на start() НЕ поднимает ни воркеров, ни рантайма,
ни фоновых сканеров. При остановке — снос всех живых песочниц и освобождение
аренд. Не критичная: ядро загружается даже если песочница деградировала.
"""
from __future__ import annotations

from pathlib import Path

from .. import obs
from ..config import settings
from . import sandbox_enabled
from .manager import SandboxManager
from .runtime import FakeRuntime

log = obs.get_logger("bossman.sandbox")

# Процессный синглтон менеджера. По умолчанию FakeRuntime — безвредно и
# детерминированно; реальные адаптеры рантайма подключаются позже. Пока фича
# выключена, рантайм всё равно ничего не исполняет (create() → SandboxDisabled).
_workspace = Path(getattr(settings, "workspace_dir", Path("."))) / "_sandbox"
MANAGER = SandboxManager(FakeRuntime(), enabled=sandbox_enabled(), workspace_root=_workspace)


class SandboxSubsystem:
    name = "sandbox"
    critical = False

    def __init__(self) -> None:
        self.manager = MANAGER

    async def validate(self) -> None:
        # Выключенная фича валидна всегда — проверять нечего.
        if not self.manager.enabled:
            return
        # Включённая: убеждаемся, что рантайм заявляет хоть какой-то tier.
        caps = self.manager.runtime.capabilities()
        if not caps.tiers:
            from .. import errors
            raise errors.IsolationUnavailable("sandbox enabled but runtime provides no isolation tier")

    async def start(self) -> None:
        if not self.manager.enabled:
            log.info("sandbox OFF — no workers/runtime/network/scanner started")
            return
        self._workspace_ready()
        log.info("sandbox ON — runtime=%s", self.manager.runtime.name)

    async def stop(self) -> None:
        # Снести все живые песочницы, освободить аренды (идемпотентно).
        await self.manager.recover()

    def _workspace_ready(self) -> None:
        self.manager.workspace_root.mkdir(parents=True, exist_ok=True)
