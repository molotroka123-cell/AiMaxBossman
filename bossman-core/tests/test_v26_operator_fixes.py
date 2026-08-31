"""V2.6 аудит, D2+D4 — Computer Operator: BROWSER имеет backend, планировщику
предлагаются только поддержанные виды действий.

D2: policy.py валидирует ActionKind.BROWSER, но production-обвязка не
регистрировала ExistingBrowserAdapter — ActionRouter падал в рантайме
«no backend supports BROWSER». Теперь адаптер в списке backend'ов.

D4: CapabilityRegistry никто не опрашивал — модели предлагался полный список
ActionKind даже для действий без backend'а на хосте. Теперь build_manager
пробрасывает пробу реестра в Planner; при ошибке пробы — откат на полный
список (degrade-open по доступности, политика всё равно фильтрует действия).
"""
from __future__ import annotations

import pytest

from bossman.computer_operator.adapters.browser import ExistingBrowserAdapter
from bossman.computer_operator.adapters.router import ActionRouter
from bossman.computer_operator.capabilities import CapabilityRegistry
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState
from bossman.computer_operator.planner import ALWAYS_KINDS, DEFAULT_KINDS, Planner
from bossman.computer_operator.subsystem import (
    _browser_toolkit_dispatch, _supported_kinds_provider, build_manager,
)

pytestmark = pytest.mark.asyncio


def _browser_action(**args):
    return ComputerAction.make(ActionKind.BROWSER, expected=ExpectedState(),
                               args={"op": "navigate", "url": "https://example.com", **args})


class _RecordingDispatch:
    """Фейковый browser-движок: только записывает, ничего не открывает."""
    def __init__(self):
        self.calls = []
    async def __call__(self, action, observation):
        self.calls.append((action, observation))


class _KindBackend:
    """Фейковый backend в стиле test_computer_capabilities: ровно заданные виды."""
    def __init__(self, name, kinds):
        self.name = name; self.kinds = set(kinds)
    async def supports(self, a, o): return a.kind in self.kinds
    async def execute(self, a, o): raise AssertionError("не должен исполняться в этих тестах")


# ---------------------------------------------------------------- D2: BROWSER backend

async def test_build_manager_registers_browser_backend(tmp_path):
    """Production-обвязка обязана иметь backend для BROWSER (раньше — не имела)."""
    dispatch = _RecordingDispatch()
    mgr = build_manager(store_path=tmp_path / "tasks.json", browser_dispatch=dispatch)
    backends = mgr.action_router.backends
    assert any(isinstance(b, ExistingBrowserAdapter) for b in backends)
    # адаптер стоит ДО общего desktop-бэкенда
    idx = next(i for i, b in enumerate(backends) if isinstance(b, ExistingBrowserAdapter))
    assert idx < len(backends) - 1


async def test_browser_action_routes_without_runtime_error(tmp_path):
    """Маршрутизация BROWSER-действия: никакого 'no backend supports BROWSER'."""
    dispatch = _RecordingDispatch()
    mgr = build_manager(store_path=tmp_path / "tasks.json", browser_dispatch=dispatch)
    a = _browser_action()
    backend = await mgr.action_router.execute(a, None)
    assert backend == "browser"
    assert dispatch.calls and dispatch.calls[0][0] is a


async def test_router_without_browser_adapter_still_fails_honestly():
    """Контроль контраста: без адаптера роутер честно падает (сам дефект)."""
    with pytest.raises(RuntimeError, match="no backend supports BROWSER"):
        await ActionRouter([_KindBackend("win", {ActionKind.CLICK})]).execute(
            _browser_action(), None)


async def test_default_dispatch_delegates_to_registered_toolkit_tool(monkeypatch):
    """Дефолтный мост: op 'navigate' -> СУЩЕСТВУЮЩИЙ инструмент browser.open,
    'op' в аргументы инструмента не протекает; второго браузера не появляется."""
    from bossman import toolkit

    seen = {}
    async def handler(args, ctx):
        seen["args"] = dict(args); seen["agent"] = ctx.agent
        return toolkit.ToolResult("ok")
    fake = toolkit.ToolDef(name="browser.open", description="", rights="read", handler=handler)
    monkeypatch.setitem(toolkit.REGISTRY, "browser.open", fake)

    await _browser_toolkit_dispatch(_browser_action(), None)
    assert seen["args"] == {"url": "https://example.com"}   # op отфильтрован
    assert seen["agent"] == "computer-operator"


async def test_default_dispatch_rejects_unknown_op_honestly():
    a = ComputerAction.make(ActionKind.BROWSER, expected=ExpectedState(),
                            args={"op": "format_disk"})
    with pytest.raises(RuntimeError, match="browser op"):
        await _browser_toolkit_dispatch(a, None)


# ---------------------------------------------------------------- D4: supported kinds

async def test_planner_offered_only_probed_kinds():
    """Проба говорит CLICK+TYPE -> в системном промпте нет APP_LAUNCH/BROWSER,
    но управляющие COMPLETE/FAIL присутствуют всегда."""
    registry = CapabilityRegistry([_KindBackend("win", {ActionKind.CLICK, ActionKind.TYPE})])

    captured = {}
    async def chat(**kw):
        captured["system"] = kw["messages"][0]["content"]
        return {"content": '{"kind":"COMPLETE","expected":{}}'}

    p = Planner(chat, supported=_supported_kinds_provider(registry))
    kinds = await p.allowed_kinds()
    assert "CLICK" in kinds and "TYPE" in kinds
    assert "APP_LAUNCH" not in kinds and "BROWSER" not in kinds
    assert ALWAYS_KINDS <= set(kinds)

    a = await p.next_action(goal="g", observation_summary="", foreground={},
                            ui_tree=None, last_result="", remaining_steps=3)
    assert a.kind is ActionKind.COMPLETE
    line = captured["system"]
    assert "Allowed kinds: " + " ".join(kinds) in line
    assert "APP_LAUNCH" not in line


async def test_probe_failure_falls_back_to_full_list():
    """Degrade-open: упавшая проба не сужает планировщик до пустоты."""
    async def boom():
        raise RuntimeError("probe exploded")
    p = Planner(lambda **kw: None, supported=boom)
    assert await p.allowed_kinds() == list(DEFAULT_KINDS)


async def test_empty_probe_falls_back_to_full_list():
    """Пустой результат пробы неотличим от сломанной — тоже полный список."""
    async def empty():
        return []
    p = Planner(lambda **kw: None, supported=empty)
    assert await p.allowed_kinds() == list(DEFAULT_KINDS)


async def test_planner_without_supported_keeps_legacy_behavior():
    """Совместимость: без параметра supported — прежний полный список."""
    assert await Planner(lambda **kw: None).allowed_kinds() == list(DEFAULT_KINDS)


async def test_build_manager_wires_registry_probe_into_planner(tmp_path):
    """Собранный менеджер: планировщик получает пробу тех же backend'ов роутера.

    На Linux WindowsDesktop честно не поддерживает ничего, поэтому из пробы
    приходят как минимум APP_LAUNCH (allowlist-адаптер) и BROWSER (D2),
    а desktop-виды вроде CLICK не предлагаются."""
    async def launcher(exe):
        raise AssertionError("проба не должна ничего запускать")
    mgr = build_manager(store_path=tmp_path / "tasks.json",
                        launcher=launcher, browser_dispatch=_RecordingDispatch())
    kinds = await mgr.planner.allowed_kinds()
    assert "BROWSER" in kinds and "APP_LAUNCH" in kinds
    assert ALWAYS_KINDS <= set(kinds)
    import sys
    if not sys.platform.startswith("win"):
        assert "CLICK" not in kinds     # нет backend'а — не предлагаем
