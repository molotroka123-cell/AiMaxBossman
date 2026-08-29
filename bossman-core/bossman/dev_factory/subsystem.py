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
        editor = None
        agent = self._production_agent()
        if agent is not None:
            from .editor import GatewayEditor
            from .planner import LLMPlanner
            self.factory.planner = LLMPlanner(agent)
            editor = GatewayEditor(agent)
            log.info("dev_factory: планировщик и редактор — модель через Gateway "
                     "(агент %s)", agent.name)
        else:
            log.info("dev_factory: планировщик FakePlanner (детерминированный режим)")
        try:
            from ..sandbox.subsystem import MANAGER as _SBX
            from .executor import SandboxExecutor
            self.factory.executor = SandboxExecutor(_SBX, editor=editor)
        except Exception as exc:  # noqa: BLE001
            log.warning("dev_factory: песочница недоступна (%s) — прогон тестов будет FAIL", exc)
        n = self.factory.recover()
        if n:
            log.info("dev_factory: восстановлено заданий: %d", len(n))

    @staticmethod
    def _production_agent():
        """Каким агентом фабрика зовёт модель (план + правка).

        BOSSMAN_DEV_FACTORY_PLANNER: fake — всегда детерминированный план;
        gateway — модель через существующий Gateway; auto (по умолчанию) —
        gateway, когда BOSSMAN_GATEWAY_URL сконфигурирован, иначе fake.
        Агента задаёт BOSSMAN_DEV_FACTORY_AGENT (по умолчанию coder); нет
        такого агента → честный fake с предупреждением, а не тихий обход.
        """
        import os

        mode = os.environ.get("BOSSMAN_DEV_FACTORY_PLANNER", "auto").strip().lower()
        if mode == "fake":
            return None
        if mode == "auto" and not settings.gateway_url:
            return None
        if mode not in ("auto", "gateway"):
            log.warning("dev_factory: неизвестный BOSSMAN_DEV_FACTORY_PLANNER=%r — fake", mode)
            return None
        from ..agents import load_all

        wanted = os.environ.get("BOSSMAN_DEV_FACTORY_AGENT", "coder")
        agents = load_all()
        agent = agents.get(wanted)
        if agent is None:
            log.warning("dev_factory: агент %r не найден — план остаётся FakePlanner", wanted)
        return agent

    async def stop(self) -> None:
        return None
