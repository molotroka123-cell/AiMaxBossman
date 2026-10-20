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
