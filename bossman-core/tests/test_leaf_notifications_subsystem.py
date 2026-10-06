"""authored_by_lane (opsplug): bossman.notifications.subsystem - boot recovery of half-sent messages + worker lifecycle."""
import asyncio

from bossman import events
from bossman.notifications import subsystem
from bossman.notifications.bridge import EventBridge
from bossman.notifications.dispatcher import NotificationDispatcher
from bossman.notifications.models import Notification, Severity
from bossman.notifications.store import SQLiteNotificationStore


class _Transport:
    def __init__(self):
        self.sent = []

    async def send(self, n):
        self.sent.append(n.id)


def test_contract_notifications_are_not_critical():
    sub = subsystem.build_subsystem()
    assert sub.name == "notifications" and sub.critical is False


async def test_start_recovers_sending_rows_then_delivers_and_stop_is_clean(tmp_path, monkeypatch):
    store = SQLiteNotificationStore(tmp_path / "n.db")
    transport = _Transport()
    bridge, dispatcher = EventBridge(store), NotificationDispatcher(store, transport)
    monkeypatch.setattr(subsystem, "STORE", store)
    monkeypatch.setattr(subsystem, "BRIDGE", bridge)
    monkeypatch.setattr(subsystem, "DISPATCHER", dispatcher)

    store.enqueue(Notification.create("x", Severity.INFO, "t", "crashed mid-send", dedupe_key="crash"))
    assert store.claim_next() is not None and store.counts() == {"sending": 1}   # a process death left it in `sending`

    sub = subsystem.build_subsystem()
    await sub.validate()
    await sub.start()
    try:
        for _ in range(100):
            if store.counts().get("sent") == 1:
                break
            await asyncio.sleep(0.02)
        assert store.counts().get("sent") == 1 and len(transport.sent) == 1     # recovered, then sent exactly once
        events.emit("task.failed", id=1, error="live bus event")
        for _ in range(100):
            if store.counts().get("sent") == 2:
                break
            await asyncio.sleep(0.02)
        assert store.counts().get("sent") == 2                                   # the bridge feeds the dispatcher
    finally:
        await sub.stop()
    assert bridge._task is None
