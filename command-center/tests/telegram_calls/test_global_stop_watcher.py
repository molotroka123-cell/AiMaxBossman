"""The calls watcher of the global STOP must always die with its task and never leave a bus subscription behind.

Regression: ``asyncio.wait_for(q.get(), 5)`` can swallow a cancellation on Python 3.11 when an event arrives at the same
moment (the watcher then lives on, subscribed, after the backend stopped: test_stop_grace_lifecycle failed intermittently).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.events import EventBus
from bcc.features import telegram_calls as feature


class FakeManager:
    def __init__(self):
        self.stops: list[str] = []
        self.active_call = None
        self.running = False
        self.shutdowns = 0

    async def stop(self, reason: str):
        self.stops.append(reason)

    async def shutdown(self):
        self.shutdowns += 1


def make():
    bus = EventBus()
    svc = SimpleNamespace(bus=bus)
    rt = SimpleNamespace(manager=FakeManager())
    return bus, svc, rt


async def test_the_watcher_ends_and_unsubscribes_when_cancelled_under_a_stream_of_events():
    for _ in range(300):
        bus, svc, rt = make()
        watcher = asyncio.create_task(feature._watch_global_stop(svc, rt))
        await asyncio.sleep(0)                                     # subscribed and waiting
        pump = asyncio.create_task(_pump(bus))
        await asyncio.sleep(0)
        watcher.cancel()
        try:
            await asyncio.wait_for(_wait(watcher), 1.0)
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        assert watcher.done(), "the watcher swallowed its cancellation"
        assert not bus._subscribers
        assert rt.manager.shutdowns == 1


async def _pump(bus):
    while True:
        await bus.emit("noise.tick")
        await asyncio.sleep(0)


async def _wait(task):
    await asyncio.wait({task})


async def test_a_global_stop_event_hangs_up_the_call_and_other_events_do_not():
    bus, svc, rt = make()
    watcher = asyncio.create_task(feature._watch_global_stop(svc, rt))
    await asyncio.sleep(0)
    await bus.emit("something.else")
    await asyncio.sleep(0.05)
    assert rt.manager.stops == []
    await bus.emit("computer.stop")
    for _ in range(50):
        if rt.manager.stops:
            break
        await asyncio.sleep(0.01)
    assert rt.manager.stops == ["computer_stop"]
    watcher.cancel()
    await asyncio.wait({watcher})
    assert not bus._subscribers
