"""rc19 audit of the Computer Use handler — each case failed on the integration HEAD.

P1-A  a timed-out `type` left its adapter thread typing; the next observation cleared
      «outcome unknown» while the orphan still typed, and the next approved action
      ran concurrently with it. An engine tool timeout (CancelledError) did not mark
      the outcome unknown at all. Long text / a model-chosen `interval` made the
      timeout reachable.
P2-C  an approved `launch` (no observation generation) survived «Stop» → «Resume»
      and a backend restart, contrary to COMPUTER_USE_THREAT_MODEL O3/O4.
P2-D  a corrupted USED_APPROVALS journal «locked» actions only until the next
      observation, which cleared the lock and then overwrote the journal.

Desktop: the FakeDesktop from test_computer_use_tools; for P1-A its TYPE runs through
the REAL WindowsDesktop._input thread with a fake pyautogui (the real mouse and
keyboard are untouched).
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from datetime import datetime, timezone

import pytest

from bcc.features import tools_computer as tc
from bcc.tools import REGISTRY, ToolContext

from .test_computer_use_tools import FakeDesktop, FakeShots, _consumed_approval

pytest.importorskip("bossman.computer_operator.models")
pytestmark = pytest.mark.timeout(120)


class _SlowPyAutoGui(types.ModuleType):
    def __init__(self):
        super().__init__("pyautogui")
        self.FAILSAFE = False
        self.written: list[str] = []

    def write(self, text, interval=0):
        time.sleep(0.02 * len(text))              # ~0.3 s per 16-character chunk
        self.written.append(text)

    def hotkey(self, *keys):
        pass


class ThreadedDesktop(FakeDesktop):
    """TYPE goes through the real adapter thread (chunks, STOP, abort, foreground)."""

    def __init__(self):
        super().__init__()
        from bossman.computer_operator.adapters.windows import WindowsDesktop
        self.real = WindowsDesktop()
        self.real._foreground_hwnd = lambda: getattr(self, "fg_handle", 1)

    def set_interrupt(self, ev):
        super().set_interrupt(ev)
        self.real.set_interrupt(ev)

    async def execute(self, a, o):
        self.executed.append((a.kind.value, a.target, a.text, dict(a.args)))
        if a.kind.value == "TYPE":
            await self.real._input(a)

    def busy(self):
        return self.real.busy()

    def abort_inflight(self):
        self.real.abort_inflight()


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


def _ctx(env, approval_id=None):
    return ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={"permissions": {}},
                       approval_id=approval_id)


async def _handler(env, args, approval_id=None):
    return await REGISTRY.get("computer.act").handler(args, _ctx(env, approval_id))


# ------------------------------------------------------------------ P1-A orphan typing

async def test_a_timed_out_typing_thread_is_stopped_and_blocks_the_next_action(env, desk, monkeypatch):
    pg = _SlowPyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", pg)
    desk.desktop = ThreadedDesktop()
    desk.desktop.set_interrupt(desk.stop)
    monkeypatch.setattr(tc, "ACT_TIMEOUT_S", 0.15)
    g = (await tc.observe(env.svc))["generation"]
    with pytest.raises(tc.ActRefused, match="исход неизвестен"):
        await tc.act(env.svc, {"action": "type", "generation": g, "text": "A" * 96})
    assert not desk.lock.locked()
    # the thread may still be finishing its current chunk: re-reading the screen
    # does NOT make the outcome known while it lives, and nothing runs beside it
    if desk.desktop.busy():
        g = (await tc.observe(env.svc))["generation"]
        assert desk.outcome_unknown
        with pytest.raises(tc.ActRefused, match="неизвестен|в фоне"):
            await tc.act(env.svc, {"action": "type", "generation": g, "text": "B"})
    deadline = time.monotonic() + 5
    while desk.desktop.busy() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert not desk.desktop.busy()
    typed = sum(len(x) for x in pg.written)
    assert typed <= 32, f"the orphan kept typing after the timeout: {typed} of 96"
    # thread gone + fresh observation → the owner's next approved step runs again
    monkeypatch.setattr(tc, "ACT_TIMEOUT_S", 60.0)
    g = (await tc.observe(env.svc))["generation"]
    assert not desk.outcome_unknown
    await tc.act(env.svc, {"action": "type", "generation": g, "text": "B"})
    assert "".join(pg.written).endswith("B") and "B" not in "".join(pg.written)[:-1]


async def test_the_typing_thread_is_told_which_window_to_type_into(env, desk):
    g = (await tc.observe(env.svc))["generation"]
    await tc.act(env.svc, {"action": "type", "generation": g, "text": "x"})
    typed = [e for e in desk.desktop.executed if e[0] == "TYPE"]
    assert typed[-1][3].get("foreground_handle") == 1


async def test_an_engine_tool_timeout_mid_action_marks_the_outcome_unknown(env, desk):
    g = (await tc.observe(env.svc))["generation"]
    desk.desktop.hang = True
    task = asyncio.ensure_future(tc.act(env.svc, {"action": "hotkey", "keys": ["ctrl", "s"],
                                                  "generation": g}))
    await asyncio.sleep(0.2)
    task.cancel()                                   # what execute_tool's wait_for does at 180 s
    with pytest.raises(asyncio.CancelledError):
        await task
    assert desk.outcome_unknown, "a cancelled effect was treated as «nothing happened»"
    assert not desk.lock.locked()
    desk.desktop.hang = False
    with pytest.raises(tc.ActRefused, match="неизвестен"):
        await tc.act(env.svc, {"action": "launch", "target": "notepad"})


async def test_text_that_cannot_be_typed_within_the_action_budget_is_refused(env, desk):
    assert tc.TYPE_BUDGET_S < tc.ACT_TIMEOUT_S
    g = (await tc.observe(env.svc))["generation"]
    for args in ({"text": "a" * 2000}, {"text": "a" * 300, "interval": 0.2},
                 {"text": "a", "interval": "fast"}, {"text": "a", "interval": float("nan")}):
        with pytest.raises(tc.ActRefused, match="символов|interval"):
            await tc.act(env.svc, {"action": "type", "generation": g, **args})
    assert desk.desktop.executed == []
    # within the budget: typed
    await tc.act(env.svc, {"action": "type", "generation": g, "text": "a" * 500})
    assert desk.desktop.doc == "a" * 500


# ------------------------------------------------------------------ P2-C launch after resume/restart

async def test_an_approved_launch_does_not_survive_resume(env, desk):
    aid = await _consumed_approval(env)          # asked + approved before STOP
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")
    res = await _handler(env, {"action": "launch", "target": "notepad"}, approval_id=aid)
    assert res.error and "до «Продолжить»" in res.content, res.content
    assert desk.desktop.executed == []


async def test_an_approved_launch_does_not_survive_a_backend_restart(env, desk, monkeypatch):
    aid = await _consumed_approval(env)
    # «restart»: a new process started after the approval was created
    monkeypatch.setattr(tc, "PROCESS_STARTED", datetime.now(timezone.utc).replace(tzinfo=None))
    env.svc._computer_state = None
    st2 = tc._owner_state(env.svc)
    st2.desktop, st2.shots = desk.desktop, FakeShots()
    res = await _handler(env, {"action": "launch", "target": "notepad"}, approval_id=aid)
    assert res.error and "перезапуска" in res.content, res.content
    assert desk.desktop.executed == []


async def test_an_approval_created_after_resume_still_executes(env, desk):
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")
    g = (await tc.observe(env.svc))["generation"]
    res = await _handler(env, {"action": "type", "text": "ok", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert not res.error, res.content
    assert desk.desktop.doc == "ok"


# ------------------------------------------------------------------ P2-D corrupted journal

async def test_a_corrupted_used_approvals_journal_is_not_unlocked_by_an_observation(env, desk):
    used = env.settings.data_dir / "computer" / tc.USED_APPROVALS_FILE
    used.parent.mkdir(parents=True, exist_ok=True)
    used.write_text("[1, 2,", encoding="utf-8")
    env.svc._computer_state = None                  # restart: the journal is read from disk
    st = tc._owner_state(env.svc)
    st.desktop, st.shots = desk.desktop, FakeShots()
    assert st.outcome_unknown and st.journal_corrupt
    g = (await tc.observe(env.svc))["generation"]
    assert st.outcome_unknown, "one observation lifted the corrupted-journal lock"
    res = await _handler(env, {"action": "type", "text": "x", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert res.error and "повреждён" in res.content
    assert used.read_text(encoding="utf-8") == "[1, 2,", "the corrupted journal was overwritten"
    assert desk.desktop.executed == []
    # the owner's «Resume» is the way out: the bad journal is set aside, not destroyed
    await env.client.post("/api/computer/resume")
    assert not st.journal_corrupt and not st.outcome_unknown
    aside = list(used.parent.glob("USED_APPROVALS.corrupt-*.json"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "[1, 2,"
    g = (await tc.observe(env.svc))["generation"]
    res = await _handler(env, {"action": "type", "text": "x", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert not res.error, res.content
