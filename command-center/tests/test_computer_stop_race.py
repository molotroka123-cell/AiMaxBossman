"""R6: the owner's STOP wins every Computer Use race (ported from d22c3095).

Checked on a fake desktop (the real mouse is never touched):
  * STOP while an action is in flight: the NEXT action, queued behind the lock, never runs;
  * STOP + instant Resume while the second action waits for the lock still cancels it
    (stop epoch) — including `launch`/`wait`, which carry no observation generation and so
    are not caught by the generation bump that Resume performs;
  * STOP + instant Resume between sub-steps of one action aborts the rest of that action;
  * after Resume the old generation is useless — a fresh observation is required;
  * STOP survives a Command Center restart;
  * the buttons are not model-callable: no tool, and a session cookie without CSRF cannot press them.
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
    """The first `gate_kind` call hangs until the test releases it: the action in flight."""

    gate_kind = "TYPE"

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, a, o):
        if a.kind.value == self.gate_kind and not self.entered.is_set():
            self.entered.set()
            await self.release.wait()
        await super().execute(a, o)


class FocusGatedDesktop(GatedDesktop):
    """Hangs on the "focus the field" sub-step, before the typing itself."""

    gate_kind = "FOCUS"


class FakeLauncher:
    def __init__(self):
        self.executed = []

    async def execute(self, a, o):
        self.executed.append(a.target)


@pytest.fixture
def gated(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    st = tc.ComputerState()
    st.stop_path = env.settings.data_dir / "computer" / tc.STOP_FILE
    # As in d22c3095: no typing interrupt wired here, so the in-flight action A is seen
    # through to its end and only the queue is under test (the adapter's mid-typing
    # interrupt is covered by test_computer_use_tools).
    st.desktop, st.shots, st.launcher = GatedDesktop(), FakeShots(), FakeLauncher()
    env.svc._computer_state = st
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    monkeypatch.setattr(tc, "LAUNCH_WAIT_S", 0.25)
    monkeypatch.setattr(tc, "_top_windows", lambda: [])
    return st


async def _queue_behind(st, make_b):
    """A is inside execute (holds the lock); B passed the entry check and waits for the lock."""
    b = asyncio.create_task(make_b())
    for _ in range(20):                    # B's entry checks are synchronous: it is at the lock
        await asyncio.sleep(0.01)
    assert not b.done() and st.lock.locked()
    return b


async def _in_flight(env, st):
    gen = (await tc.observe(env.svc))["generation"]
    a = asyncio.create_task(tc.act(env.svc, {"action": "type", "generation": gen, "text": "A"}))
    await asyncio.wait_for(st.desktop.entered.wait(), 5)
    return a, gen


async def test_stop_during_in_flight_action_blocks_the_queued_one(env, gated):
    a, gen = await _in_flight(env, gated)
    b = await _queue_behind(gated, lambda: tc.act(env.svc, {"action": "type", "generation": gen,
                                                            "text": "B"}))
    status = (await env.client.get("/api/computer/status")).json()
    assert status["busy"] is True and status["stopped"] is False

    assert (await env.client.post("/api/computer/stop")).json()["stopped"] is True
    gated.desktop.release.set()

    await a                                            # the action in flight completes
    with pytest.raises(tc.ActRefused, match="Стоп"):
        await b                                        # the queued one never starts
    texts = [t for kind, _, t, _ in gated.desktop.executed if kind == "TYPE"]
    assert texts == ["A"], texts
    assert (await env.client.get("/api/computer/status")).json()["stopped"] is True


async def test_stop_then_instant_resume_still_cancels_the_queued_action(env, gated):
    a, gen = await _in_flight(env, gated)
    b = await _queue_behind(gated, lambda: tc.act(env.svc, {"action": "type", "generation": gen,
                                                            "text": "B"}))
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")      # the owner changed their mind instantly
    gated.desktop.release.set()
    await a                                            # the action in flight completes
    with pytest.raises(tc.ActRefused, match="ждало очереди"):
        await b
    assert [t for k, _, t, _ in gated.desktop.executed if k == "TYPE"] == ["A"]


@pytest.mark.parametrize("queued", [{"action": "launch", "target": "notepad"},
                                    {"action": "wait", "seconds": 0}])
async def test_stop_then_instant_resume_cancels_queued_launch_and_wait(env, gated, queued):
    """launch/wait have no generation, so Resume's generation bump does not catch them."""
    a, _ = await _in_flight(env, gated)
    snapshots_before = []

    async def counting_snapshot(orig=gated.desktop.snapshot):
        snapshots_before.append(1)
        return await orig()

    b = await _queue_behind(gated, lambda: tc.act(env.svc, dict(queued)))
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")
    gated.desktop.release.set()
    await a                                            # A re-observes after its own effect
    gated.desktop.snapshot = counting_snapshot         # from here on, only B could observe
    with pytest.raises(tc.ActRefused, match="ждало очереди"):
        await b
    assert gated.launcher.executed == [], "a launch queued before STOP ran after it"
    assert snapshots_before == [], "a wait queued before STOP ran (and re-observed) after it"


async def test_stop_between_substeps_aborts_the_rest_of_the_action(env, gated):
    gated.desktop = FocusGatedDesktop()
    gen = (await tc.observe(env.svc))["generation"]
    a = asyncio.create_task(tc.act(env.svc, {"action": "type", "generation": gen, "text": "секрет"}))
    await asyncio.wait_for(gated.desktop.entered.wait(), 5)
    await env.client.post("/api/computer/stop")
    gated.desktop.release.set()
    with pytest.raises(tc.ActRefused):
        await a
    assert [k for k, *_ in gated.desktop.executed] == ["FOCUS"]   # typing never started


async def test_stop_and_instant_resume_between_substeps_aborts_the_rest(env, gated):
    gated.desktop = FocusGatedDesktop()
    gen = (await tc.observe(env.svc))["generation"]
    a = asyncio.create_task(tc.act(env.svc, {"action": "type", "generation": gen, "text": "секрет"}))
    await asyncio.wait_for(gated.desktop.entered.wait(), 5)
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")
    gated.desktop.release.set()
    with pytest.raises(tc.ActRefused, match="Стоп"):
        await a
    assert [k for k, *_ in gated.desktop.executed] == ["FOCUS"]   # typing never started


async def test_resume_requires_fresh_observation(env, gated):
    gated.desktop.entered.set()                        # no gate
    gated.desktop.release.set()
    gen = (await tc.observe(env.svc))["generation"]
    await env.client.post("/api/computer/stop")
    obs = await tc.observe(env.svc)                    # reading while stopped is allowed
    assert obs["stopped"] is True
    with pytest.raises(tc.ActRefused, match="Стоп"):
        await tc.act(env.svc, {"action": "type", "generation": obs["generation"], "text": "x"})
    body = (await env.client.post("/api/computer/resume")).json()
    assert body["stopped"] is False
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
    assert gated.launcher.executed == []


async def test_stop_survives_restart(env, gated):
    await env.client.post("/api/computer/stop")
    marker = env.settings.data_dir / "computer" / tc.STOP_FILE
    assert marker.exists()

    app2, svc2 = await start_app(make_settings(env.settings.data_dir.parent), start_workers=False)
    try:
        async with client_for(app2, svc2) as c2:
            assert (await c2.get("/api/computer/status")).json()["stopped"] is True
            st2 = tc._owner_state(svc2)
            st2.desktop, st2.shots, st2.launcher = FakeDesktop(), FakeShots(), FakeLauncher()
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
    # A session cookie without the CSRF header is refused: a foreign page cannot press the button.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://test") as anon:
        assert (await anon.post("/api/computer/stop")).status_code == 401
        login = await anon.post("/api/login", json={"token": env.svc.auth.token})
        assert login.status_code == 200
        assert (await anon.post("/api/computer/resume")).status_code == 403
        ok = await anon.post("/api/computer/stop", headers={"X-BCC-CSRF": login.json()["csrf"]})
        assert ok.status_code == 200 and ok.json()["stopped"] is True
