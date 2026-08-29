"""Production wiring Dev Factory: режим планировщика fake|gateway|auto.

Раньше LLMPlanner существовал как класс, но подсистема всегда ставила
FakePlanner. Теперь режим выбирается явно и безопасно (fail к fake, а не тихий
обход), редактор подключается тем же агентом через существующий Gateway.
"""
from __future__ import annotations

import pytest

from bossman.config import settings
from bossman.dev_factory.planner import FakePlanner, LLMPlanner
from bossman.dev_factory.subsystem import DevFactorySubsystem


class _Agent:
    name = "coder"


def test_fake_mode_returns_no_agent(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "fake")
    assert DevFactorySubsystem._production_agent() is None


def test_auto_without_gateway_url_is_fake(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "auto")
    monkeypatch.setattr(settings, "gateway_url", "", raising=False)
    assert DevFactorySubsystem._production_agent() is None


def test_gateway_mode_loads_agent(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "gateway")
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_AGENT", "coder")
    monkeypatch.setattr("bossman.dev_factory.subsystem.load_all",
                        lambda: {"coder": _Agent()}, raising=False)
    # load_all импортируется внутри метода — патчим по месту вызова
    import bossman.agents as agents_mod
    monkeypatch.setattr(agents_mod, "load_all", lambda: {"coder": _Agent()})
    agent = DevFactorySubsystem._production_agent()
    assert agent is not None and agent.name == "coder"


def test_missing_agent_falls_back_to_fake(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "gateway")
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_AGENT", "ghost")
    import bossman.agents as agents_mod
    monkeypatch.setattr(agents_mod, "load_all", lambda: {"coder": _Agent()})
    assert DevFactorySubsystem._production_agent() is None


def test_unknown_mode_falls_back_to_fake(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "nonsense")
    assert DevFactorySubsystem._production_agent() is None


@pytest.mark.anyio
async def test_start_wires_llm_planner_when_agent_present(monkeypatch, tmp_path):
    from bossman.dev_factory.factory import DevFactory

    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "gateway")
    import bossman.agents as agents_mod
    monkeypatch.setattr(agents_mod, "load_all", lambda: {"coder": _Agent()})

    sub = DevFactorySubsystem()
    sub.factory = DevFactory(tmp_path / "f")
    await sub.start()
    assert isinstance(sub.factory.planner, LLMPlanner)
    assert sub.factory.executor is not None
    assert sub.factory.executor.editor is not None


@pytest.mark.anyio
async def test_start_keeps_fake_planner_in_fake_mode(monkeypatch, tmp_path):
    from bossman.dev_factory.factory import DevFactory

    monkeypatch.setenv("BOSSMAN_DEV_FACTORY_PLANNER", "fake")
    sub = DevFactorySubsystem()
    sub.factory = DevFactory(tmp_path / "f")
    await sub.start()
    assert isinstance(sub.factory.planner, FakePlanner)
    assert sub.factory.executor.editor is None


@pytest.fixture
def anyio_backend():
    return "asyncio"
