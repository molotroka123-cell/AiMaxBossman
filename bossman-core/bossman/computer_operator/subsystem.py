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
from .adapters.browser import ExistingBrowserAdapter
from .adapters.router import ActionRouter
from .adapters.screenshot import LocalScreenshotProvider
from .adapters.vision import VisionInputAdapter
from .adapters.windows import WindowsDesktop
from .capabilities import CapabilityRegistry
from .manager import ComputerOperatorManager
from .models import TaskState
from .obligations import file_probe
from .observer import Observer
from .policy import authorize_computer_control
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


# Синонимы op планировщика -> имя СУЩЕСТВУЮЩЕГО инструмента browser.* (policy.py
# валидирует op=="navigate", в toolkit исторически это browser.open).
_BROWSER_OP_ALIASES = {"navigate": "open"}


async def _browser_toolkit_dispatch(action, observation):
    """Мост BROWSER-действий к СУЩЕСТВУЮЩЕМУ browser-toolkit (V2.6, D2).

    Второго браузера и второй политики не появляется: `op` из args уходит в уже
    зарегистрированный инструмент `browser.<op>` со всеми его стенами
    (blocked/sensitive-домены, отказ от submit без confirmed_* и т.д.).
    Неизвестный op — честная ошибка-данные для replan, а не тихий no-op.
    """
    from ..toolkit import REGISTRY, ToolContext
    op = str((action.args or {}).get("op") or "").strip().lower()
    tool = REGISTRY.get(f"browser.{_BROWSER_OP_ALIASES.get(op, op)}") if op else None
    if tool is None:
        raise RuntimeError(f"browser op is not a registered browser tool: {op!r}")
    args = {k: v for k, v in (action.args or {}).items() if k != "op"}
    if action.target and "target" not in args:
        args["target"] = action.target
    # A3-01 (P1, security). Здесь вызывается `tool.handler` НАПРЯМУЮ, минуя
    # runner, а подтверждение владельца поднимает именно runner по флагу
    # `confirm_default`. Инструменты browser.confirmed_* объявлены с этим флагом
    # и внутри пропускают собственные проверки (они и созданы для случая «уже
    # подтверждено»), поэтому через этот путь необратимое действие уходило БЕЗ
    # согласия. Замыкал цикл сам toolkit: он советует планировщику «используй
    # browser.confirmed_click», планировщик повторяет — и стены больше нет.
    #
    # Внешняя policy тут не подстраховывает: она поднимает approval по лексикону
    # улик, а «Enter» на обычном домене в нём отсутствует.
    if getattr(tool, "confirm_default", False):
        raise RuntimeError(
            f"browser op requires owner approval and cannot be dispatched directly: {op!r}")
    ctx = ToolContext(agent="computer-operator",
                      workdir=Path(getattr(settings, "workspace_dir", ".")))
    result = await tool.handler(args, ctx)
    if getattr(result, "error", False):
        raise RuntimeError(f"browser.{op}: {(result.content or '')[:400]}")
    return result


class AuthorizedComputerOperatorManager(ComputerOperatorManager):
    """A2-03/A2-04: разрешение спрашивается НА ГРАНИЦЕ ЭФФЕКТА, а не только при
    создании задачи.

    Базовый менеджер зовёт `access_check` ровно один раз — в `create_task`.
    Между созданием строки и отправкой ввода в рабочий стол проходит план
    модели, ожидание подтверждения владельца, пауза, перезапуск процесса с
    `recover_all`, «Продолжить» из UI. Всё это ВХОДЫ, на которых согласия уже
    может не быть: тумблер `computer_control` выключен, профиль выключен или
    удалён, профильный gate вообще не поднят. Проверка при создании таким
    входом не является — задача продолжала действовать по разрешению, которого
    больше нет.

    Точек две, и обе обязательны:
    * `run()` — вход в исполнение по УЖЕ существующей строке (resume/recovery),
      который раньше не проверялся вовсе;
    * `_save()` при переходе в RUNNING — последняя запись перед
      `action_router.execute`, то есть сама граница эффекта. Разрешение,
      снятое во время планирования или ожидания подтверждения, останавливает
      действие ДО адаптера.

    Проверяется `source` и `owner_device_id` САМОЙ строки задачи: понизить
    источник на границе (выдать удалённый вход за локальный) нельзя, потому
    что здесь он не передаётся заново, а читается из журнала. Ошибка источника
    авторизации — отказ (см. `authorize_computer_control`).
    """

    def _authorize_effect(self, t) -> None:
        try:
            authorize_computer_control(self.access_check, t.owner_device_id, t.source)
        except PermissionError as exc:
            raise PermissionError(f"computer control denied at effect boundary: {exc}") from exc

    async def run(self, task_id):
        try:
            t = self._req(task_id)
        except KeyError:
            return await super().run(task_id)
        try:
            self._authorize_effect(t)
        except PermissionError as exc:
            # Отказ владельца — это не падение оператора: строка получает
            # честную причину, а рабочего стола действие не касается.
            return self._fail(t, str(exc))
        return await super().run(task_id)

    def _save(self, t):
        if t.state is TaskState.RUNNING:
            self._authorize_effect(t)
        return super()._save(t)


def _supported_kinds_provider(registry: CapabilityRegistry):
    """Ленивый поставщик поддержанных ActionKind для планировщика (V2.6, D4).

    Проба идёт по РЕАЛЬНО зарегистрированным адаптерам; ошибка пробы не роняет
    планирование — Planner.allowed_kinds() сам откатывается на полный список
    (degrade-open по доступности; политика всё равно фильтрует действия).
    """
    async def provider():
        caps = await registry.probe()
        return sorted({c.action.value for c in caps if c.supported and c.action})
    return provider


def build_manager(*, store_path=None, launcher=None, browser_dispatch=None) -> ComputerOperatorManager:
    """Собрать менеджер на РЕАЛЬНЫХ компонентах.

    Платформенная недоступность честно проявляется на вызове (WindowsDesktop
    сам откажет вне Windows), а не подменяется заглушкой, которая делает вид,
    что рабочий стол есть.
    """
    desktop = WindowsDesktop()
    # Порядок: специфичные по виду/источнику раньше общего desktop-бэкенда.
    # BROWSER обслуживает существующий browser-toolkit (V2.6, D2: раньше
    # ActionKind.BROWSER проходил policy, но роутер не имел backend'а и падал).
    backends = [
        AppLaunchAdapter(launcher=launcher),
        ExistingBrowserAdapter(browser_dispatch or _browser_toolkit_dispatch),
        VisionInputAdapter(desktop),
        desktop,
    ]
    return AuthorizedComputerOperatorManager(
        store=JsonTaskStore(store_path or default_store_path()),
        # V2.6, D4: планировщику предлагаются только виды с реальным backend'ом
        # на этом хосте (CapabilityRegistry опрашивает те же адаптеры роутера).
        planner=Planner(planner_chat, model_alias=PLANNER_ALIAS,
                        supported=_supported_kinds_provider(CapabilityRegistry(backends))),
        observer=Observer(desktop, LocalScreenshotProvider()),
        action_router=ActionRouter(backends),
        approval_create=approvals.create, approval_wait=approvals.wait,
        event_emit=events.emit,
        # Профильный gate: устройство с выключенным тумблером computer_control
        # НЕ создаёт desktop-задачу (no-op, если profiles-сервис не поднят).
        access_check=_profile_access_check,
        # AT-01: исход именных обязательств цели читается с НАСТОЯЩЕГО диска,
        # независимо от модели и от экрана. Без этого порта слой обязательств
        # существовал бы только в тестах, а прод закрывался бы по-старому —
        # «была какая-то подтверждённая мутация».
        obligation_probe=file_probe(Path.home()))


MANAGER = build_manager()


class ComputerOperatorSubsystem:
    name = "computer_operator"; critical = False
    async def validate(self): return None
    async def start(self): MANAGER.recover_all()
    async def stop(self): return None


def build_subsystem(): return ComputerOperatorSubsystem()
