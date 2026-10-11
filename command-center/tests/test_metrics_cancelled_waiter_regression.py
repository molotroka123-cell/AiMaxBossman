"""Regression for the Python 3.14 cancelled-waiter telemetry failure.

A caller may cancel while the shared blocking read is still running.  The
underlying read must stay alive for other/future callers, a later reader error
must be retrieved exactly once by the sampler rather than reported by an
orphan wrapper Future, and the next request must be able to recover.
"""
from __future__ import annotations

import asyncio
import gc
import threading

import pytest

from bcc import metrics


class ControlledRead:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.started: asyncio.Event | None = None
        self.release = threading.Event()
        self.calls = 0
        self.fail = True

    def bind(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()

    def __call__(self) -> dict:
        assert self.loop is not None and self.started is not None
        self.calls += 1
        self.loop.call_soon_threadsafe(self.started.set)
        if not self.release.wait(2):
            raise TimeoutError("fixture reader was not released")
        if self.fail:
            raise OSError("fixture late telemetry failure")
        return {
            "ts": metrics.utcnow(),
            "cpu_pct": 0.0,
            "ram_used_mb": 1.0,
            "ram_total_mb": 2.0,
            "disk_used_gb": None,
            "disk_total_gb": None,
            "gpu": None,
        }


def test_cancelled_only_waiter_does_not_cancel_or_poison_shared_read(monkeypatch):
    async def run() -> None:
        sampler = metrics.MetricsSampler(None, None)
        reader = ControlledRead()
        reader.bind()
        monkeypatch.setattr(sampler, "read", reader)

        loop = asyncio.get_running_loop()
        errors: list[dict] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        try:
            waiter = asyncio.create_task(sampler.read_async())
            assert reader.started is not None
            await asyncio.wait_for(reader.started.wait(), 5)
            pending = sampler._pending_read
            assert pending is not None and not pending.done()

            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not pending.cancelled(), "caller cancellation leaked into the shared read"

            finished = asyncio.Event()
            pending.add_done_callback(lambda _: finished.set())
            reader.release.set()
            await asyncio.wait_for(finished.wait(), 5)
            for _ in range(4):
                await asyncio.sleep(0)

            assert sampler._pending_read is None
            assert reader.calls == 1
            # Do not call pending.exception() here: production owns retrieval of
            # the late exception.  Dropping the final reference is what exposes
            # an orphaned wrapper-Future regression to the loop handler.
            del pending
            gc.collect()
            for _ in range(4):
                await asyncio.sleep(0)
            assert not errors, errors

            reader.fail = False
            reader.release.set()
            recovered = await sampler.read_async()
            assert recovered["ram_total_mb"] == 2.0
            assert reader.calls == 2
        finally:
            reader.release.set()
            loop.set_exception_handler(previous)

    asyncio.run(run())
