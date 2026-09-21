"""Computer Use в Command Center (owner audit 2026-09-21: провайдера не было вовсе).

Плоскость решений проверяется на макете рабочего стола: свежесть наблюдения,
«Стоп», координатный запасной путь с повторным наблюдением, постусловие,
последствийные действия через ASK. Живой прогон на Блокноте — отдельно
(owner-repair/evidence/computer-use-live.*), здесь реальную мышь не трогаем.
"""
from __future__ import annotations

import pytest

from bcc.features import tools_computer as tc
from bcc.tools import REGISTRY, decide_effect

pytest.importorskip("bossman.computer_operator.models")


class FakeDesktop:
    def __init__(self):
        self.title = "Безымянный — Блокнот"
        self.doc = ""
        self.executed = []

    async def snapshot(self):
        return ({"title": self.title, "app": "Notepad", "handle": 1},
                {"elements": [
                    {"name": "Текстовый редактор", "control_type": "Document", "value": self.doc,
                     "left": 0, "top": 100, "right": 800, "bottom": 600, "x": 400, "y": 350},
                    {"name": "Файл", "control_type": "MenuItem",
                     "left": 0, "top": 0, "right": 50, "bottom": 30, "x": 25, "y": 15},
                    {"name": "Удалить", "control_type": "Button",
                     "left": 60, "top": 0, "right": 120, "bottom": 30, "x": 90, "y": 15}]})

    async def execute(self, a, o):
        self.executed.append((a.kind.value, a.target, a.text, dict(a.args)))
        if a.kind.value == "TYPE":
            self.doc += a.text or ""

    def set_interrupt(self, ev):
        pass

    async def foreground(self):
        return {"title": self.title, "handle": getattr(self, "fg_handle", 1)}


class FakeShots:
    async def capture(self):
        return None, False


@pytest.fixture
def desk(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    st = tc.ComputerState()
    st.desktop, st.shots, st.launcher = FakeDesktop(), FakeShots(), None
    env.svc._computer_state = st
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    return st


async def test_observe_act_verify_cycle(env, desk):
    obs = await tc.observe(env.svc)
    assert obs["generation"] == 1 and obs["window"]["title"].endswith("Блокнот")
    res = await tc.act(env.svc, {"action": "type", "generation": 1, "text": "Привет, мир 42",
                                 "expect": {"contains_text": "Привет, мир 42"}})
    assert res["verified"] is True and res["after_generation"] == 2
    res = await tc.act(env.svc, {"action": "type", "generation": 2, "text": "x",
                                 "expect": {"contains_text": "нет такого текста"}})
    assert res["verified"] is False
    res = await tc.act(env.svc, {"action": "hotkey", "generation": 3, "keys": ["ctrl", "s"]})
    assert res["verified"] is None and "не задано" in res["checks"][0]


async def test_stale_generation_is_refused_nothing_executed(env, desk):
    await tc.observe(env.svc)
    await tc.observe(env.svc)
    with pytest.raises(tc.ActRefused, match="устарело"):
        await tc.act(env.svc, {"action": "type", "generation": 1, "text": "x"})
    with pytest.raises(tc.ActRefused, match="устарело"):
        await tc.act(env.svc, {"action": "type", "text": "x"})
    assert desk.desktop.executed == []


async def test_owner_stop_blocks_until_resume(env, desk):
    await tc.observe(env.svc)
    assert (await env.client.post("/api/computer/stop")).json() == {"stopped": True}
    with pytest.raises(tc.ActRefused, match="Стоп"):
        await tc.act(env.svc, {"action": "type", "generation": 1, "text": "x"})
    assert desk.desktop.executed == []
    await env.client.post("/api/computer/resume")
    res = await tc.act(env.svc, {"action": "type", "generation": 1, "text": "ok",
                                 "expect": {"contains_text": "ok"}})
    assert res["verified"] is True


async def test_coordinate_fallback_requires_name_and_fresh_hit(env, desk):
    await tc.observe(env.svc)
    # голые координаты без цели — политика bossman-core отказывает
    with pytest.raises(tc.ActRefused, match="политика"):
        await tc.act(env.svc, {"action": "click", "generation": 1, "x": 25, "y": 15})
    # координаты без явного fallback — тоже отказ
    with pytest.raises(tc.ActRefused, match="политика"):
        await tc.act(env.svc, {"action": "click", "generation": 1, "target": "Файл", "x": 25, "y": 15})
    # точка вне названного элемента на СВЕЖЕМ экране — отказ, клика нет
    with pytest.raises(tc.ActRefused, match="не попадают"):
        await tc.act(env.svc, {"action": "click", "generation": 1, "target": "Файл",
                               "x": 400, "y": 350, "coordinate_fallback": True})
    assert desk.desktop.executed == []
    gen = desk.generation
    res = await tc.act(env.svc, {"action": "click", "generation": gen, "target": "Файл",
                                 "x": 25, "y": 15, "coordinate_fallback": True})
    assert desk.desktop.executed[-1][0] == "CLICK"
    assert res["after_generation"] == gen + 2          # повторное наблюдение до и после


async def test_consequential_target_needs_declared_semantic_and_ask(env, desk):
    await tc.observe(env.svc)
    with pytest.raises(tc.ActRefused, match="semantic"):
        await tc.act(env.svc, {"action": "click", "generation": 1, "target": "Удалить"})
    assert desk.desktop.executed == []
    spec = REGISTRY.get("computer.act")
    granted = {"permissions": {"computer.control": True}}
    assert decide_effect(spec, {"action": "click", "target": "Удалить", "semantic": "delete"},
                         granted)[0] == "ask"
    assert decide_effect(spec, {"action": "type", "text": "x"}, granted)[0] == "auto"
    assert decide_effect(spec, {"action": "type", "text": "x"}, {"permissions": {}})[0] == "ask"


async def test_launch_is_allowlist_only(env, desk):
    with pytest.raises(tc.ActRefused, match="allowlist"):
        await tc.act(env.svc, {"action": "launch", "target": "cmd.exe /c del *"})


async def test_status_endpoint_is_honest(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (False, "управление рабочим столом доступно только на Windows"))
    body = (await env.client.get("/api/computer/status")).json()
    assert body["available"] is False and "Windows" in body["detail"]
    assert "computer.act" in body["tools"]


async def test_input_refused_when_focus_moved_to_other_window(env, desk):
    """Живой дефект 2026-09-21: набор ушёл в «Параметры». Теперь — отказ без ввода."""
    await tc.observe(env.svc)
    desk.desktop.fg_handle = 999
    with pytest.raises(tc.ActRefused, match="фокус ушёл"):
        await tc.act(env.svc, {"action": "type", "generation": 1, "text": "секрет"})
    assert desk.desktop.executed == []
