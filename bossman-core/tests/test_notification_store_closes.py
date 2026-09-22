"""The notification store closes every SQLite connection it opens.

``with sqlite3.connect(...) as c`` only commits or rolls back; it does not close.
The store relied on the garbage collector, so a kept ``CallbackRejected`` (its
traceback holds the frame with ``c``) kept notifications.db open, and on Windows
the data directory could not be removed (owner scenario OS-39).
"""
import sqlite3

import pytest

from bossman.notifications import store as store_module
from bossman.notifications.models import ActionKind, Notification, NotificationAction, Severity
from bossman.notifications.store import CallbackRejected, SQLiteNotificationStore


def _is_open(connection) -> bool:
    try:
        connection.total_changes
    except sqlite3.ProgrammingError:
        return False
    return True


@pytest.fixture
def opened(monkeypatch):
    connections = []
    real = sqlite3.connect

    def tracking(*args, **kwargs):
        connection = real(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(store_module.sqlite3, "connect", tracking)
    return connections


def test_every_call_closes_its_connection_even_when_the_refusal_is_kept(tmp_path, opened):
    store = SQLiteNotificationStore(tmp_path / "n.db")
    store.enqueue(Notification.create("x", Severity.INFO, "t", "b", dedupe_key="k"))
    claimed = store.claim_next()
    store.mark_sent(claimed.id)
    store.counts()
    raw = store.create_callback(NotificationAction(ActionKind.APPROVE, "approval", "1", "ok", "fp"), "42")
    with pytest.raises(CallbackRejected) as refused:
        store.consume_callback(raw, "43")
    kept = refused.value  # the scenario keeps the refusal, and with it the traceback
    assert store.consume_callback(raw, "42")["target_id"] == "1"
    assert opened, "the store opened no connection at all"
    assert [c for c in opened if _is_open(c)] == [], "a connection outlived its call"
    assert kept.__traceback__ is not None
