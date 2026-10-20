"""Branch 6 «Computer»: reliability/safety hardening (2026-10-20).

Everything here runs on fakes: the real mouse, keyboard and screen are never touched,
no real screenshot is taken and Telegram is never opened.
"""
from __future__ import annotations

import asyncio

import pytest

from bcc.features import tools_computer as tc

from .test_computer_use_tools import FakeDesktop, FakeShots

pytest.importorskip("bossman.computer_operator.models")


@pytest.fixture
def desk(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    st = tc.ComputerState()
    st.stop_path = env.settings.data_dir / "computer" / tc.STOP_FILE
    st.desktop, st.shots, st.launcher = FakeDesktop(), FakeShots(), None
    st.desktop.set_interrupt(st.stop)
    env.svc._computer_state = st
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    return st


# ------------------------------------------------------------------ 1. STOP mid-action, uncached

async def test_stop_file_written_by_another_process_interrupts_typing_mid_action(env, desk):
    """The persisted STOP file is the authority. A STOP written by another process
    (second backend, owner CLI, pult) while a long text is being typed must cut the
    typing at the next chunk, not after the whole text has gone into the window."""
    g = (await tc.observe(env.svc))["generation"]
    long_text = "абвг" * 50

    async def typing():
        return await tc.act(env.svc, {"action": "type", "generation": g, "text": long_text})

    job = asyncio.create_task(typing())
    for _ in range(200):
        if desk.desktop.doc:
            break
        await asyncio.sleep(0.005)
    assert desk.desktop.doc, "typing never started"
    # Not set_stop(): only the file appears, exactly as another process would write it.
    desk.stop_path.parent.mkdir(parents=True, exist_ok=True)
    desk.stop_path.write_text("cli\n", encoding="utf-8")
    res = await asyncio.gather(job, return_exceptions=True)
    assert isinstance(res[0], RuntimeError) and "interrupted typing" in str(res[0])
    assert len(desk.desktop.doc) < len(long_text)


async def test_resume_after_file_stop_really_clears_the_interrupt(env, desk):
    desk.stop_path.parent.mkdir(parents=True, exist_ok=True)
    desk.stop_path.write_text("cli\n", encoding="utf-8")
    assert desk.stop.is_set() is True
    await env.client.post("/api/computer/resume")
    assert not desk.stop_path.exists()
    assert desk.stop.is_set() is False
    g = (await tc.observe(env.svc))["generation"]
    res = await tc.act(env.svc, {"action": "type", "generation": g, "text": "ok",
                                 "expect": {"contains_text": "ok"}})
    assert res["verified"] is True


# ------------------------------------------------------------------ 2. takeover resume = successful observe

class UnreadableDesktop(FakeDesktop):
    """UIA did not answer: the adapter returns a window with `error` and no handle."""

    async def snapshot(self):
        return {"title": "", "app": "", "error": "COMError"}, None


async def test_takeover_resume_keeps_stop_when_the_screen_was_not_read(env, desk):
    await env.client.post("/api/computer/takeover/pause", json={"reason": "2FA"})
    desk.desktop = UnreadableDesktop()
    r = await env.client.post("/api/computer/takeover/resume", json={})
    assert r.status_code == 409
    assert desk.stopped() is True and desk.stop_path.is_file()
    assert desk.takeover_active is True
    out = await tc.resume_after_takeover(env.svc)
    assert out["stopped"] is True and out["refused"]
    assert desk.stopped() is True
    assert any(e["phase"] == "takeover_resume_refused" for e in desk.step_log)


async def test_takeover_resume_keeps_stop_when_owner_postcondition_fails(env, desk):
    await env.client.post("/api/computer/takeover/pause", json={"reason": "manual"})
    r = await env.client.post("/api/computer/takeover/resume",
                              json={"expect": {"contains_text": "ПОДТВЕРЖДЕНО ВЛАДЕЛЬЦЕМ"}})
    assert r.status_code == 409
    assert desk.stopped() is True and desk.takeover_active is True
    # positive control: the owner finishes the manual step, the same resume clears STOP
    desk.desktop.doc = "ПОДТВЕРЖДЕНО ВЛАДЕЛЬЦЕМ"
    r = await env.client.post("/api/computer/takeover/resume",
                              json={"expect": {"contains_text": "ПОДТВЕРЖДЕНО ВЛАДЕЛЬЦЕМ"}})
    assert r.status_code == 200 and r.json()["verified"] is True
    assert desk.stopped() is False and desk.takeover_active is False


# ------------------------------------------------------------------ 5. every input step carries the window identity

@pytest.mark.parametrize("args", [
    {"action": "hotkey", "keys": ["ctrl", "s"]},
    {"action": "scroll", "clicks": -2},
    {"action": "click", "target": "Файл"},                       # UIA path
    {"action": "click", "target": "Файл", "x": 25, "y": 15, "coordinate_fallback": True},
    {"action": "type", "text": "abc", "replace": True},          # incl. Ctrl+A and focus sub-steps
])
async def test_every_desktop_input_carries_the_observed_window_handle(env, desk, args):
    """The adapter can only verify the window right before the effect when the caller
    tells it which window was checked; earlier only TYPE carried `foreground_handle`."""
    g = (await tc.observe(env.svc))["generation"]
    await tc.act(env.svc, {**args, "generation": g})
    assert desk.desktop.executed, "nothing was dispatched"
    for kind, target, _text, a_args in desk.desktop.executed:
        assert a_args.get("foreground_handle") == 1, (kind, target, a_args)


# ------------------------------------------------------------------ 6. postcondition on an unread screen

@pytest.mark.parametrize("obs", [
    {"window": {"title": "", "error": "COMError"}, "elements": []},
    {"window": {"title": "(окно вне разрешения задачи — содержимое скрыто)"}, "elements": [],
     "hidden": True},
])
def test_screen_postcondition_on_unread_or_hidden_window_is_not_verified(obs):
    ok, notes = tc.verify(obs, {"absent_text": "Ошибка сохранения"})
    assert ok is False, notes
    assert any("НЕ подтверждены" in n for n in notes)
    assert tc.verify(obs, {"window_title_contains": "окно"})[0] is False


def test_screen_postcondition_positive_control_on_a_read_window():
    obs = {"window": {"title": "a.txt — Блокнот", "handle": 1},
           "elements": [{"name": "Текстовый редактор", "value": "всё сохранено"}]}
    assert tc.verify(obs, {"absent_text": "Ошибка сохранения"})[0] is True
    assert tc.verify(obs, {"contains_text": "сохранено"})[0] is True


async def test_act_on_an_unreadable_after_screen_is_not_reported_verified(env, desk):
    g = (await tc.observe(env.svc))["generation"]

    async def broken_snapshot():
        return {"title": "", "app": "", "error": "COMError"}, None

    async def act_then_break():
        orig = desk.desktop.execute

        async def execute(a, o):
            await orig(a, o)
            desk.desktop.snapshot = broken_snapshot   # the post-action re-read fails
        desk.desktop.execute = execute
        return await tc.act(env.svc, {"action": "hotkey", "keys": ["ctrl", "s"], "generation": g,
                                      "expect": {"absent_text": "Ошибка сохранения"}})

    res = await act_then_break()
    assert res["verified"] is False


# ------------------------------------------------------------------ 7. secrets out of the step log and bus

async def test_secrets_from_screen_and_expect_never_reach_step_log_or_bus(env, desk, monkeypatch):
    import json

    token = "ghp_" + "Ab3" * 12                       # built at runtime: not a real key
    api_key = "sk-" + "Zx9" * 9
    events: list[tuple[str, dict]] = []
    orig_emit = env.svc.bus.emit

    async def recording_emit(event, **kw):
        events.append((event, kw))
        return await orig_emit(event, **kw)

    monkeypatch.setattr(env.svc.bus, "emit", recording_emit)
    desk.desktop.title = f"{token} — Блокнот"            # e.g. a .env opened in Notepad
    g = (await tc.observe(env.svc))["generation"]
    await tc.act(env.svc, {"action": "hotkey", "keys": ["ctrl", "s"], "generation": g,
                           "expect": {"contains_text": api_key}})
    await tc.pause_for_takeover(env.svc, reason=f"enter {api_key} manually")
    blob = json.dumps({"log": desk.step_log, "events": events}, ensure_ascii=False, default=str)
    assert token not in blob and token[:20] not in blob
    assert api_key not in blob and api_key[:12] not in blob
    assert "REDACTED" in blob
    # positive control: ordinary text survives
    assert "Блокнот" in blob
