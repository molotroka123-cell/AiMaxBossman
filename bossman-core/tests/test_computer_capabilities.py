"""Capability discovery: реестр НЕ врёт о возможностях хоста."""
from __future__ import annotations

import pytest

from bossman.computer_operator.capabilities import (
    CAPABILITY_ACTIONS, CapabilityRegistry,
)
from bossman.computer_operator.models import ActionKind

pytestmark = pytest.mark.asyncio


class _Backend:
    """Фейковый backend: поддерживает ровно заданные виды действий."""
    def __init__(self, name, kinds, boom=False):
        self.name = name; self.kinds = set(kinds); self.boom = boom
    async def supports(self, a, o):
        if self.boom:
            raise RuntimeError("backend exploded")
        return a.kind in self.kinds
    async def execute(self, a, o):
        raise AssertionError("probe must never execute an action")


async def test_no_backends_means_nothing_supported():
    caps = await CapabilityRegistry([]).probe()
    assert caps and all(c.supported is False for c in caps)
    assert all(c.reason for c in caps if not c.supported)


async def test_only_real_backend_kinds_are_reported_supported():
    reg = CapabilityRegistry([_Backend("win", {ActionKind.CLICK, ActionKind.TYPE})])
    caps = {c.name: c for c in await reg.probe()}
    assert caps["computer.mouse.click"].supported is True
    assert caps["computer.mouse.click"].backend == "win"
    assert caps["computer.keyboard.type"].supported is True
    # не заявленные backend'ом — честно unsupported
    assert caps["computer.application.launch"].supported is False
    assert "no backend" in caps["computer.application.launch"].reason


async def test_unknown_capability_is_denied_by_default():
    reg = CapabilityRegistry([_Backend("win", set(ActionKind))])
    assert await reg.is_supported("computer.telepathy.read_mind") is False


async def test_broken_backend_does_not_count_as_support():
    """Упавший supports() != поддержка (fail-closed)."""
    reg = CapabilityRegistry([_Backend("broken", {ActionKind.CLICK}, boom=True)])
    caps = {c.name: c for c in await reg.probe()}
    assert caps["computer.mouse.click"].supported is False


async def test_probe_never_executes_actions():
    """Проба обязана быть безопасной: execute() у backend не вызывается."""
    reg = CapabilityRegistry([_Backend("win", {ActionKind.CLICK})])
    await reg.probe()          # _Backend.execute бросил бы AssertionError


async def test_supported_names_is_subset_of_declared_vocabulary():
    reg = CapabilityRegistry([_Backend("win", {ActionKind.CLICK, ActionKind.FOCUS})])
    names = await reg.supported_names()
    assert set(names) <= set(CAPABILITY_ACTIONS)
    assert "computer.window.focus" in names


async def test_vision_only_backend_does_not_fake_structured_support():
    """Проба идёт с source='planner', поэтому vision-адаптер её не перехватывает."""
    from bossman.computer_operator.adapters.vision import VisionInputAdapter

    class _Desktop:
        async def execute(self, a, o): raise AssertionError("must not execute")

    reg = CapabilityRegistry([VisionInputAdapter(_Desktop())])
    caps = {c.name: c for c in await reg.probe()}
    # vision принимает только source="vision" -> структурной поддержки НЕТ
    assert caps["computer.mouse.click"].supported is False


async def test_probe_target_is_capability_appropriate_no_false_negative():
    """APP_LAUNCH сужает supports() по allowlist: нейтральная проба дала бы
    ЛОЖНОЕ 'не поддерживается'. Проба обязана брать реальное allowlisted-имя."""
    from bossman.computer_operator.adapters.app_launch import AppLaunchAdapter
    from bossman.computer_operator.applist import APP_ALLOWLIST
    from bossman.computer_operator.capabilities import _probe_target

    assert _probe_target(ActionKind.APP_LAUNCH) in APP_ALLOWLIST
    reg = CapabilityRegistry([AppLaunchAdapter()])
    caps = {c.name: c for c in await reg.probe()}
    assert caps["computer.application.launch"].supported is True
    assert caps["computer.application.launch"].backend == "app-launch"


async def test_probe_target_does_not_weaken_allowlist_enforcement():
    """Проба не должна становиться лазейкой: execute всё равно бьёт по allowlist."""
    from bossman.computer_operator.adapters.app_launch import AppLaunchAdapter
    from bossman.computer_operator.models import ComputerAction, ExpectedState

    adapter = AppLaunchAdapter()
    bad = ComputerAction.make(ActionKind.APP_LAUNCH, expected=ExpectedState(),
                              target="/bin/sh", source="planner")
    assert await adapter.supports(bad, None) is False        # не allowlisted
    with pytest.raises(RuntimeError):
        await adapter.execute(bad, None)
