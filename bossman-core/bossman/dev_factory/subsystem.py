"""Stage 10 — подсистема жизненного цикла Dev Factory."""
from __future__ import annotations

from pathlib import Path

from .. import obs
from ..config import settings
from .factory import DevFactory

log = obs.get_logger("bossman.dev_factory")

_root = Path(getattr(settings, "workspace_dir", Path("."))) / "_dev_factory"
FACTORY = DevFactory(_root)


class DevFactorySubsystem:
    name = "dev_factory"
    critical = False

    def __init__(self) -> None:
        self.factory = FACTORY

    async def validate(self) -> None:
        return None

    async def start(self) -> None:
        # Исполнитель берёт песочницу Этапа 8 — второго рантайма не заводим.
        try:
            from ..sandbox.subsystem import MANAGER as _SBX
            from .executor import SandboxExecutor
            self.factory.executor = SandboxExecutor(_SBX)
        except Exception as exc:  # noqa: BLE001
            log.warning("dev_factory: песочница недоступна (%s) — прогон тестов будет FAIL", exc)
        n = self.factory.recover()
        if n:
            log.info("dev_factory: восстановлено заданий: %d", len(n))

    async def stop(self) -> None:
        return None
