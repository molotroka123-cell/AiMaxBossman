"""Browser STOP must answer in bounded time even when Playwright's close does not (12.10 sweep: the stop request
hung past the UI's ten seconds on a saturated machine).  The session is gone for every caller at once; the close
finishes - or is recorded as not clean - in the background; a new start waits for it.
"""
from __future__ import annotations

import asyncio
import time

from bcc.v2.browser_control import BrowserManager, BrowserRuntimeSession


class Closable:
    def __init__(self, *, hang: asyncio.Event | None = None, fail: bool = False):
        self.hang, self.fail, self.closed = hang, fail, False

    async def close(self):
        if self.hang is not None:
            await self.hang.wait()
        if self.fail:
            raise RuntimeError("target closed")
        self.closed = True


def _manager(tmp_path, context, browser=None) -> BrowserManager:
    manager = BrowserManager(tmp_path / "browser")
    manager._sessions[1] = BrowserRuntimeSession(id=1, policy=None, context=context, page=object(), browser=browser)
    return manager


async def test_a_hanging_close_does_not_hang_the_stop_request(tmp_path, monkeypatch):
    monkeypatch.setattr(BrowserManager, "CLOSE_WAIT_S", 0.2)
    gate = asyncio.Event()
    context = Closable(hang=gate)
    manager = _manager(tmp_path, context)
    t0 = time.monotonic()
    await manager.stop(1)
    assert time.monotonic() - t0 < 2.0                        # answered, though the close is still stuck
    assert manager.is_live(1) is False                        # and the session is unusable immediately
    assert 1 in manager._closing and not context.closed
    gate.set()                                                # the browser finally lets go
    await manager._wait_closing(5)
    assert context.closed and manager._closing == {} and manager.close_problems == {}


async def test_a_close_that_never_finishes_is_recorded_not_awaited_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(BrowserManager, "CLOSE_WAIT_S", 0.05)
    monkeypatch.setattr(BrowserManager, "CLOSE_HARD_S", 0.1)
    manager = _manager(tmp_path, Closable(hang=asyncio.Event()), Closable())
    await manager.stop(1)
    await manager._wait_closing(5)
    assert "context.close не завершился" in manager.close_problems[1]


async def test_a_failing_close_is_recorded_and_never_raises(tmp_path):
    browser = Closable()
    manager = _manager(tmp_path, Closable(fail=True), browser)
    await manager.stop(1)                                     # no exception reaches the HTTP handler
    assert browser.closed is True                             # the browser is still closed after a context failure
    assert "context.close: RuntimeError" in manager.close_problems[1]


async def test_a_normal_stop_still_closes_everything_before_it_returns(tmp_path):
    context, browser = Closable(), Closable()
    manager = _manager(tmp_path, context, browser)
    await manager.stop(1)
    assert context.closed and browser.closed and manager._closing == {} and manager.close_problems == {}
    await manager.stop(1)                                     # a second stop of the same id is a no-op
    await manager.close()                                     # shutdown with nothing left is fine too
