"""Audit-round fixes of CallSession: STOP while ringing, unconfirmed hangup, mechanical summary after STOP."""
from __future__ import annotations

import asyncio
import time

from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.types import CallState, Outcome, UNCERTAIN_OUTCOMES

from .test_session import build, until


class SlowDialTransport(LoopbackTransport):
    """dial() rings for a long time and only ends when cancelled (like a real ringing call)."""

    def __init__(self, ring=30.0, **kw):
        super().__init__(**kw)
        self.ring, self.dial_cancelled = ring, False

    async def dial(self, peer, *, ring_timeout):
        self.dial_calls += 1
        try:
            await asyncio.sleep(self.ring)
        except asyncio.CancelledError:
            self.dial_cancelled = True
            raise


async def test_stop_while_ringing_cancels_the_dial_and_hangs_up_promptly():
    s, t, *_ = build(transport=SlowDialTransport(30.0))
    task = asyncio.create_task(s.run())
    assert await until(lambda: t.dial_calls == 1, 2)
    t0 = time.monotonic()
    assert await s.stop_and_wait(timeout=1) is True
    assert time.monotonic() - t0 < 1.0
    assert t.hangup_calls >= 1 and t.dial_cancelled and t.dial_calls == 1
    rec = await task
    assert rec.outcome == Outcome.STOPPED


async def test_owner_hangup_while_ringing_also_cancels_the_dial():
    s, t, *_ = build(transport=SlowDialTransport(30.0))
    task = asyncio.create_task(s.run())
    assert await until(lambda: t.dial_calls == 1, 2)
    s.hangup()
    rec = await asyncio.wait_for(task, 2)
    assert t.hangup_calls >= 1 and t.dial_cancelled and rec.outcome == Outcome.COMPLETED


async def test_dial_that_finishes_normally_is_not_disturbed_by_the_race():
    s, t, *_ = build(transport=SlowDialTransport(0.05))
    task = asyncio.create_task(s.run())
    assert await until(lambda: s.record.state == CallState.ACTIVE, 2)
    assert not t.dial_cancelled and t.hangup_calls == 0
    s.hangup()
    await task


class BadHangup(LoopbackTransport):
    async def hangup(self, reason="local"):
        self.hangup_calls += 1
        raise RuntimeError("no confirmation")


async def test_unconfirmed_hangup_makes_the_outcome_uncertain():
    s, t, *_ = build(transport=BadHangup())
    task = asyncio.create_task(s.run())
    assert await until(lambda: s.record.state == CallState.ACTIVE, 2)
    s.hangup()
    rec = await task
    assert rec.outcome in UNCERTAIN_OUTCOMES
    assert rec.counters["hangup_confirmed"] == 0


async def test_confirmed_hangup_keeps_the_outcome():
    s, t, *_ = build()
    task = asyncio.create_task(s.run())
    assert await until(lambda: s.record.state == CallState.ACTIVE, 2)
    s.hangup()
    rec = await task
    assert rec.outcome == Outcome.COMPLETED and rec.counters["hangup_confirmed"] == 1


async def test_summary_is_mechanical_when_stopping_even_if_outcome_is_not_stopped():
    s, t, stt, tts, brain = build()
    s._stopping = True
    s._outcome = Outcome.COMPLETED
    from bcc.telegram_calls.types import Turn
    s._history.append(Turn("user", "привет"))
    summary = await s._summary()
    assert summary.generated_by == "mechanical" and brain.summarize_calls == 0
    s2, _t2, _a, _b, brain2 = build()
    s2._outcome = Outcome.COMPLETED
    s2._history.append(Turn("user", "привет"))
    assert (await s2._summary()).generated_by == "fixture-llm" and brain2.summarize_calls == 1
