"""Jeff 1.1: the inbox survives a restart with no loss and no duplicates.

A "restart" is: close the store, open a new PITStore on the same home (what a
new poller process does), call recover() as ParticipantRuntime.run() does."""
from __future__ import annotations

from bcc.pit.runtime import PITStore

WHO = "person-a"


def open_store(home) -> PITStore:
    store = PITStore(home)
    store.recover()
    return store


def phases(store: PITStore) -> dict[int, str]:
    return {row["id"]: row["phase"] for row in store.db.execute("SELECT id,phase FROM inbox")}


def body(n: int) -> dict:
    return {"text": f"message {n}", "_user_id": 1, "_chat_id": 1}


def test_pending_messages_survive_restart_and_are_processed_once(tmp_path):
    store = open_store(tmp_path)
    for n in range(1, 4):
        assert store.ingest(n, WHO, body(n)) is True
    store.close()

    store = open_store(tmp_path)
    assert phases(store) == {1: "pending", 2: "pending", 3: "pending"}
    seen = []
    while (item := store.claim(WHO, "chat")) is not None:
        seen.append(item[0])
        store.finish(item[0], "done")
    assert seen == [1, 2, 3]
    assert store.claim(WHO, "chat") is None


def test_redelivered_updates_after_restart_are_not_duplicated(tmp_path):
    store = open_store(tmp_path)
    for n in (1, 2):
        store.ingest(n, WHO, body(n))
    store.close()

    store = open_store(tmp_path)
    # Telegram re-sends everything from the old offset after a crash
    assert store.ingest(1, WHO, body(1)) is False
    assert store.ingest(2, WHO, body(2)) is False
    assert store.ingest(3, WHO, body(3)) is True
    assert sorted(phases(store)) == [1, 2, 3]
    assert store.get("offset") == 4


def test_in_flight_message_is_never_replayed_and_never_silently_lost(tmp_path):
    store = open_store(tmp_path)
    for n in (1, 2):
        store.ingest(n, WHO, body(n))
    claimed = store.claim(WHO, "chat")           # crash while message 1 is being answered
    assert claimed[0] == 1
    store.close()

    store = open_store(tmp_path)
    states = phases(store)
    assert states[1] == "interrupted_unknown"    # kept and named, so it can be reconciled
    assert states[2] == "pending"
    assert store.claim(WHO, "chat")[0] == 2      # the next one proceeds; 1 is not answered twice
    assert store.claim(WHO, "chat") is None


def test_every_ingested_update_ends_in_exactly_one_state_across_repeated_crashes(tmp_path):
    store = open_store(tmp_path)
    for n in range(1, 5):
        store.ingest(n, WHO, body(n))
    claimed = []
    for _ in range(2):
        item = store.claim(WHO, "chat")
        claimed.append(item[0])
        store.finish(item[0], "done")
    store.claim(WHO, "chat")                     # 3 is in flight at the crash
    store.close()

    store = open_store(tmp_path)
    for n in range(1, 8):                        # redelivery overlaps, then new updates arrive
        store.ingest(n, WHO, body(n))
    while (item := store.claim(WHO, "chat")) is not None:
        claimed.append(item[0])
        store.finish(item[0], "done")
    store.close()

    store = open_store(tmp_path)
    final = phases(store)
    assert sorted(final) == [1, 2, 3, 4, 5, 6, 7]                # nothing lost, nothing added twice
    assert len(claimed) == len(set(claimed))                     # nothing answered twice
    assert final[3] == "interrupted_unknown"
    assert all(final[n] == "done" for n in final if n != 3)


# -- back-pressure: a full lane defers Telegram updates instead of dropping them -------------
def _update(n: int) -> dict:
    return {"update_id": n, "message": {
        "message_id": n, "text": f"hello {n}",
        "from": {"id": 101, "is_bot": False}, "chat": {"id": 101, "type": "private"}}}


def test_full_lane_defers_update_instead_of_dropping_it(tmp_path):
    from .test_pit_runtime import make_runtime

    runtime = make_runtime(tmp_path)
    who = runtime.settings.people[0].key
    results = [runtime._ingest_update(_update(n)) for n in range(1, 5)]
    assert results == [True, True, True, True] and runtime.store.get("offset") == 5
    assert runtime._ingest_update(_update(5)) is False           # lane full: deferred
    assert runtime.store.get("offset") == 5                      # offset NOT advanced: Telegram keeps it
    for _ in range(4):                                           # workers drain the lane
        item = runtime.store.claim(who, "chat")
        runtime.store.finish(item[0], "done")
    assert runtime._ingest_update(_update(5)) is True            # the same update is accepted later
    assert runtime.store.get("offset") == 6
    assert sorted(phases(runtime.store)) == [1, 2, 3, 4, 5]
    runtime.store.close()


def test_poll_loop_stops_the_batch_at_a_deferred_update_and_asks_for_it_again(tmp_path, monkeypatch):
    import asyncio

    import pytest

    from bcc.pit import runtime as rt
    from .test_pit_runtime import make_runtime

    runtime = make_runtime(tmp_path)
    requested: list[int] = []

    async def call(method, payload):
        requested.append(payload["offset"])
        if len(requested) == 1:
            return [_update(n) for n in range(1, 7)]
        (runtime.home / rt.STOP_FLAG).write_text("x")
        return []

    async def no_sleep(_seconds):
        return None

    runtime.telegram.call = call
    runtime.catalog_checked_at = 10 ** 12
    monkeypatch.setattr(rt.asyncio, "sleep", no_sleep)
    with pytest.raises(rt.StopRequested):
        asyncio.run(runtime._poll())
    assert requested == [0, 5]                                    # 5 and 6 are requested again
    assert sorted(phases(runtime.store)) == [1, 2, 3, 4]
    runtime.store.close()
