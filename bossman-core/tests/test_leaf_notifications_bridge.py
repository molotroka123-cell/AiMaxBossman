"""authored_by_lane (opsplug): bossman.notifications.bridge - the one subscriber on the existing event bus."""
import asyncio
import json

from bossman import events
from bossman.notifications.bridge import EventBridge
from bossman.notifications.store import SQLiteNotificationStore


async def _wait(pred, n=100):
    for _ in range(n):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_bus_events_become_deduplicated_queue_rows_and_noise_is_ignored(tmp_path):
    store = SQLiteNotificationStore(tmp_path / "n.db")
    bridge = EventBridge(store)
    await bridge.start()
    try:
        events.emit("budget.warning", scope="daily_global", subject="2026-10-06", projected_usd="0.80", limit_usd="1.00")
        events.emit("budget.warning", scope="daily_global", subject="2026-10-06", projected_usd="0.80", limit_usd="1.00")
        events.emit("some.random.event", x=1)               # no policy maps it: must not enqueue
        events.emit("task.failed", id=7, error="boom")
        assert await _wait(lambda: sum(store.counts().values()) >= 2)
        await asyncio.sleep(0.1)
    finally:
        await bridge.stop()
    assert sum(store.counts().values()) == 2, store.counts()    # duplicate warning collapsed, noise dropped


async def test_garbage_on_the_queue_never_kills_the_subscriber(tmp_path):
    store = SQLiteNotificationStore(tmp_path / "n.db")
    bridge = EventBridge(store)
    await bridge.start()
    try:
        bridge._queue.put_nowait("not json at all {")
        bridge._queue.put_nowait(json.dumps({"kind": "task.completed", "id": 3, "summary": "ok"}))
        assert await _wait(lambda: store.counts().get("pending") == 1)
    finally:
        await bridge.stop()


async def test_stop_unsubscribes_cancels_and_is_idempotent(tmp_path):
    store = SQLiteNotificationStore(tmp_path / "n.db")
    bridge = EventBridge(store)
    await bridge.start()
    q = bridge._queue
    assert q in events._subscribers
    await bridge.stop()
    assert q not in events._subscribers and bridge._task is None and bridge._queue is None
    await bridge.stop()                                       # second stop is a no-op
    events.emit("task.failed", id=9, error="after stop")
    await asyncio.sleep(0.1)
    assert sum(store.counts().values()) == 0
