"""R6 (2026-09-22): «Стоп» владельца выигрывает гонки Computer Use.

Проверяется на макете рабочего стола (реальную мышь не трогаем):
  * «Стоп» во время действия в полёте не пускает СЛЕДУЮЩЕЕ, стоящее в очереди;
  * «Стоп» + мгновенное «Продолжить», пока второе действие ждёт замок, всё
    равно отменяет второе (эпоха стопа);
  * после «Продолжить» старое generation не годится — нужно свежее наблюдение;
  * «Стоп» переживает перезапуск Command Center;
  * кнопки не доступны модели: инструмента нет, а кукой без CSRF их не нажать.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from bcc.features import tools_computer as tc
from bcc.tools import REGISTRY

from .conftest import client_for, make_settings, start_app
from .test_computer_use_tools import FakeDesktop, FakeShots

pytest.importorskip("bossman.computer_operator.models")


class GatedDesktop(FakeDesktop):
    """Первый набор текста «зависает», пока тест не отпустит — это действие в полёте.

    (Подшаги до набора — фокус поля — «Стоп» тоже обрывает; это отдельный тест.)"""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, a, o):
        if a.kind.value == "TYPE" and not self.entered.is_set():
            self.entered.set()
            await self.release.wait()
        await super().execute(a, o)


@pytest.fixture
def gated(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    st = tc.ComputerState()
    st.desktop, st.shots, st.launcher = GatedDesktop(), FakeShots(), None
    env.svc._computer_state = st
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    return st


async def _in_flight_and_queued(env, st):
    """A — в execute, B — ждёт замок. Возвращает обе задачи."""
    gen = (await tc.observe(env.svc))["generation"]
    a = asyncio.create_task(tc.act(env.svc, {"action": "type", "generation": gen, "text": "A"}))
    await asyncio.wait_for(st.desktop.entered.wait(), 5)
    b = asyncio.create_task(tc.act(env.svc, {"action": "type", "generation": gen, "text": "B"}))
    for _ in range(50):                      # B прошёл входную проверку и встал за замок
        if st.busy >= 2:
            break
        await asyncio.sleep(0.01)
    assert st.busy == 2 and st.lock.locked()
    return a, b


async def test_stop_during_in_flight_action_blocks_the_queued_one(env, gated):
    a, b = await _in_flight_and_queued(env, gated)
    status = (await env.client.get("/api/computer/status")).json()
    assert status["busy"] is True and status["active"] is True and status["stopped"] is False

    body = (await env.client.post("/api/computer/stop")).json()
    assert body["stopped"] is True and body["busy"] is True
    gated.desktop.release.set()

    await a                                            # действие в полёте досмотрено
    with pytest.raises(tc.ActRefused, match="Стоп"):
        await b                                        # очередь — нет
    texts = [t for kind, _, t, _ in gated.desktop.executed if kind == "TYPE"]
    assert texts == ["A"], texts
    assert (await env.client.get("/api/computer/status")).json()["stopped"] is True


async def test_stop_then_instant_resume_still_cancels_the_queued_action(env, gated):
    a, b = await _in_flight_and_queued(env, gated)
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")      # владелец передумал мгновенно
    gated.desktop.release.set()
    await a
    with pytest.raises(tc.ActRefused, match="ждало очереди"):
        await b
    assert [t for k, _, t, _ in gated.desktop.executed if k == "TYPE"] == ["A"]


class FocusGatedDesktop(FakeDesktop):
    """Зависает на подшаге «фокус поля» — до самого набора."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, a, o):
        if a.kind.value == "FOCUS" and not self.entered.is_set():
            self.entered.set()
            await self.release.wait()
        await super().execute(a, o)


async def test_stop_between_substeps_aborts_the_rest_of_the_action(env, gated):
    gated.desktop = FocusGatedDesktop()
    gen = (await tc.observe(env.svc))["generation"]
    a = asyncio.create_task(tc.act(env.svc, {"action": "type", "generation": gen, "text": "секрет"}))
    await asyncio.wait_for(gated.desktop.entered.wait(), 5)
    await env.client.post("/api/computer/stop")
    gated.desktop.release.set()
    with pytest.raises(tc.ActRefused):
        await a
    assert [k for k, *_ in gated.desktop.executed] == ["FOCUS"]   # набор не начался


async def test_resume_requires_fresh_observation(env, gated):
    gated.desktop.entered.set()                        # без задержки
    gated.desktop.release.set()
    gen = (await tc.observe(env.svc))["generation"]
    await env.client.post("/api/computer/stop")
    # наблюдать во время стопа можно (только чтение), действовать — нет
    obs = await tc.observe(env.svc)
    assert obs["stopped"] is True
    with pytest.raises(tc.ActRefused, match="Стоп"):
        await tc.act(env.svc, {"action": "type", "generation": obs["generation"], "text": "x"})
    body = (await env.client.post("/api/computer/resume")).json()
    assert body == {"stopped": False, "needs_observe": True}
    for stale in (gen, obs["generation"]):
        with pytest.raises(tc.ActRefused, match="устарело"):
            await tc.act(env.svc, {"action": "type", "generation": stale, "text": "x"})
    assert gated.desktop.executed == []
    fresh = (await tc.observe(env.svc))["generation"]
    res = await tc.act(env.svc, {"action": "type", "generation": fresh, "text": "ok",
                                 "expect": {"contains_text": "ok"}})
    assert res["verified"] is True


async def test_launch_and_wait_are_also_stopped(env, gated):
    await env.client.post("/api/computer/stop")
    for args in ({"action": "wait", "seconds": 0}, {"action": "launch", "target": "notepad"}):
        with pytest.raises(tc.ActRefused, match="Стоп"):
            await tc.act(env.svc, args)


async def test_stop_survives_restart(env, gated):
    await env.client.post("/api/computer/stop")
    marker = env.settings.data_dir / "computer" / "STOP"
    assert marker.exists()

    app2, svc2 = await start_app(make_settings(env.settings.data_dir.parent), start_workers=False)
    try:
        async with client_for(app2, svc2) as c2:
            status = (await c2.get("/api/computer/status")).json()
            assert status["stopped"] is True and status["stopped_at"]
            st2 = svc2._computer_state
            st2.desktop, st2.shots, st2.launcher = FakeDesktop(), FakeShots(), None
            gen = (await tc.observe(svc2))["generation"]
            with pytest.raises(tc.ActRefused, match="Стоп"):
                await tc.act(svc2, {"action": "type", "generation": gen, "text": "x"})
            assert st2.desktop.executed == []
            assert (await c2.post("/api/computer/resume")).json()["stopped"] is False
            assert not marker.exists()
    finally:
        await svc2.stop()

    app3, svc3 = await start_app(make_settings(env.settings.data_dir.parent), start_workers=False)
    try:
        async with client_for(app3, svc3) as c3:
            assert (await c3.get("/api/computer/status")).json()["stopped"] is False
    finally:
        await svc3.stop()


async def test_stop_and_resume_are_not_model_callable(env):
    names = set(REGISTRY.names())
    computer = {n for n in names if n.startswith("computer.")}
    assert computer == {"computer.observe", "computer.act"}
    assert not any("stop" in n or "resume" in n for n in computer)
    assert "stop" not in tc.KINDS and "resume" not in tc.KINDS
    # Кука сессии без CSRF-заголовка — отказ: страница чужого сайта кнопку не нажмёт.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://test") as anon:
        assert (await anon.post("/api/computer/stop")).status_code == 401
        login = await anon.post("/api/login", json={"token": env.svc.auth.token})
        assert login.status_code == 200
        assert (await anon.post("/api/computer/resume")).status_code == 403
        ok = await anon.post("/api/computer/stop", headers={"X-BCC-CSRF": login.json()["csrf"]})
        assert ok.status_code == 200 and ok.json()["stopped"] is True
