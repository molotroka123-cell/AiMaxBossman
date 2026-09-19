from __future__ import annotations

import asyncio

import pytest

from bcc.events import EventBus


class _CancelledSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement):
        raise asyncio.CancelledError()

    async def commit(self):
        raise AssertionError("commit is unreachable after cancelled execute")


class _CancelledDB:
    def session(self):
        return _CancelledSession()


async def test_fenced_out_diagnostic_persistence_is_best_effort_under_cancellation():
    bus = EventBus(_CancelledDB())  # type: ignore[arg-type]
    q = bus.subscribe()

    msg = await bus.emit("run.fenced_out", run_id=7, fence=3, reason="stale worker")

    assert msg["kind"] == "run.fenced_out"
    assert q.get_nowait()["run_id"] == 7


async def test_normal_event_cancellation_still_propagates():
    bus = EventBus(_CancelledDB())  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        await bus.emit("task.completed", task_id=1)
