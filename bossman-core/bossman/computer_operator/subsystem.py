"""Production wiring Stage 13 — реальный путь исполнения, а не каркас.

Цепочка: команда -> Planner (модель ТОЛЬКО через llm.chat, то есть Stage 3
Gateway) -> Observer (реальный рабочий стол) -> policy -> ActionRouter ->
платформенный executor -> свежее наблюдение -> verifier.

Второго клиента к провайдеру, второй шины событий и второго approval-движка
здесь не появляется: планировщик ходит через существующий llm.chat, события —
через events.emit, подтверждения — через approvals.create/wait.
"""
from pathlib import Path

from .. import approvals, events
from ..config import settings
from .adapters.app_launch import AppLaunchAdapter
from .adapters.router import ActionRouter
from .adapters.screenshot import LocalScreenshotProvider
from .adapters.vision import VisionInputAdapter
from .adapters.windows import WindowsDesktop
from .manager import ComputerOperatorManager
from .observer import Observer
from .planner import Planner
from .store import JsonTaskStore

# Планировщик рабочего стола — локальный алиас: управление машиной владельца не
# должно молча уезжать в облако. cloud_policy=never держит это инвариантом.
PLANNER_ALIAS = str(getattr(settings, "computer_operator_model", "") or "bossman-fast")


def _planner_agent():
    from ..agents import AgentSpec
    return AgentSpec(name="computer-operator", title="Computer Operator planner",
                     model=PLANNER_ALIAS, cloud_policy="never")


async def planner_chat(*, model, messages, max_tokens=None):
    """Единственный выход планировщика к модели — существующий llm.chat.

    При заданном BOSSMAN_GATEWAY_URL это Stage 3 Gateway (маршрутизация,
    cloud_policy, Cost Governor). Прямого HTTP к провайдеру здесь нет.
    """
    from .. import llm
    return await llm.chat(_planner_agent(), messages, alias=model, max_tokens=max_tokens)


def default_store_path() -> Path:
    return Path(getattr(settings, "workspace_dir", ".")) / "computer_operator" / "tasks.json"


def _profile_access_check(device_id, source="local"):
    """Ленивый мост к profiles.service (без жёсткого импорта/цикла).

    Бросает profiles.gate.CapabilityDenied / ProfilesUnavailable (обе —
    PermissionError), если управление компом запрещено ИЛИ (для не-локального
    источника) профильный gate недоступен. Локальный хозяин без профиля — как
    раньше, no-op (Security Hardening V1.1, H2/H7 fail-closed для не-локальных).
    Полное отсутствие пакета profiles тоже fail-closed для не-локального источника.
    """
    try:
        from ..profiles.service import computer_access_check
    except Exception as exc:  # noqa: BLE001 — пакет недоступен
        if source != "local":
            raise PermissionError(
                f"profiles package unavailable; computer control denied for source {source!r}") from exc
        return
    computer_access_check(device_id, source)


def build_manager(*, store_path=None, launcher=None) -> ComputerOperatorManager:
    """Собрать менеджер на РЕАЛЬНЫХ компонентах.

    Платформенная недоступность честно проявляется на вызове (WindowsDesktop
    сам откажет вне Windows), а не подменяется заглушкой, которая делает вид,
    что рабочий стол есть.
    """
    desktop = WindowsDesktop()
    return ComputerOperatorManager(
        store=JsonTaskStore(store_path or default_store_path()),
        planner=Planner(planner_chat, model_alias=PLANNER_ALIAS),
        observer=Observer(desktop, LocalScreenshotProvider()),
        # Порядок: специфичные по виду/источнику раньше общего desktop-бэкенда.
        action_router=ActionRouter([
            AppLaunchAdapter(launcher=launcher),
            VisionInputAdapter(desktop),
            desktop,
        ]),
        approval_create=approvals.create, approval_wait=approvals.wait,
        event_emit=events.emit,
        # Профильный gate: устройство с выключенным тумблером computer_control
        # НЕ создаёт desktop-задачу (no-op, если profiles-сервис не поднят).
        access_check=_profile_access_check)


MANAGER = build_manager()


class ComputerOperatorSubsystem:
    name = "computer_operator"; critical = False
    async def validate(self): return None
    async def start(self): MANAGER.recover_all()
    async def stop(self): return None


def build_subsystem(): return ComputerOperatorSubsystem()
