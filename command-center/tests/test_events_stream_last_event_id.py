"""/api/events/stream: a native EventSource reconnect names its cursor only in the
`Last-Event-ID` header. Without `after` in the URL that header is the cursor:
missed events come back once, in order, and nothing already shown comes twice.
An explicit `after` wins over the header; a non-numeric header is ignored.

The ASGI app is driven directly (as in test_events_stream_reconnect.py): httpx's
ASGITransport buffers a whole response and would never return from an endless
stream. Frames are parsed WITHOUT de-duplication, so a duplicate would be seen."""
from __future__ import annotations

import asyncio
import json

from bcc import api as api_mod
from bcc.auth import HEADER

from .conftest import make_settings, start_app

TASK = 91


async def collect(app, query, token, until, *, last_event_id=None, timeout=10.0):
    """One GET of the stream; returns [(sse id or None, frame)] in arrival order.
    `until(frames)` -> True makes the client disconnect."""
    buffer = ""
    frames: list[tuple[str | None, dict]] = []
    disconnect = asyncio.Event()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        nonlocal buffer
        if msg["type"] != "http.response.body" or not msg.get("body"):
            return
        buffer += msg["body"].decode()
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            lines = block.splitlines()
            data = [line[6:] for line in lines if line.startswith("data: ")]
            ids = [line[4:] for line in lines if line.startswith("id: ")]
            if data:
                frames.append((ids[0] if ids else None, json.loads(data[0])))
        if until(frames):
            disconnect.set()

    headers = [(b"host", b"test"), (HEADER.lower().encode(), token.encode())]
    if last_event_id is not None:
        headers.append((b"last-event-id", str(last_event_id).encode()))
    path = "/api/events/stream"
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": headers,
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    await asyncio.wait_for(app(scope, receive, send), timeout)
    return frames


def notes(frames) -> list[int]:
    return [f["n"] for _, f in frames if f.get("kind") == "task.note"]


def has_note(n):
    return lambda frames: n in notes(frames)


async def emit_later(svc, *numbers, delay=0.3):
    await asyncio.sleep(delay)
    for n in numbers:
        await svc.bus.emit("task.note", task_id=TASK, n=n)


async def test_last_event_id_replays_missed_events_once_without_after(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.05)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        first = await svc.bus.emit("task.note", task_id=TASK, n=1)
        await svc.bus.emit("task.note", task_id=TASK, n=2)
        await svc.bus.emit("task.note", task_id=TASK, n=3)
        feeder = asyncio.create_task(emit_later(svc, 4))
        frames = await collect(app, f"task_id={TASK}", svc.auth.token, has_note(4),
                               last_event_id=first["seq"])
        await feeder
        assert notes(frames) == [2, 3, 4], frames            # each missed one once, then live
        opened = frames[0][1]
        assert opened["kind"] == "stream.open" and opened["after"] == first["seq"]
        assert [f["kind"] for _, f in frames].count("stream.replayed") == 1
        for sse_id, frame in frames:
            if frame.get("kind") == "task.note":
                assert sse_id == str(frame["seq"]), "the SSE id is the cursor the browser sends back"
    finally:
        await svc.stop()


async def test_explicit_after_wins_over_the_header(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.05)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        first = await svc.bus.emit("task.note", task_id=TASK, n=1)
        second = await svc.bus.emit("task.note", task_id=TASK, n=2)
        await svc.bus.emit("task.note", task_id=TASK, n=3)
        feeder = asyncio.create_task(emit_later(svc, 4))
        frames = await collect(app, f"task_id={TASK}&after={second['seq']}", svc.auth.token, has_note(4),
                               last_event_id=first["seq"])
        await feeder
        assert notes(frames) == [3, 4], frames
        assert frames[0][1]["after"] == second["seq"]
    finally:
        await svc.stop()


async def test_a_non_numeric_header_is_ignored(tmp_path, monkeypatch):
    """Negative control: without a usable cursor the stream is live-only, as before."""
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.05)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        await svc.bus.emit("task.note", task_id=TASK, n=1)
        for bad in ("abc", "-5", "１２"):                     # full-width digits are not a cursor either
            feeder = asyncio.create_task(emit_later(svc, 2, delay=0.2))
            frames = await collect(app, f"task_id={TASK}", svc.auth.token, has_note(2), last_event_id=bad)
            await feeder
            assert notes(frames) == [2], (bad, frames)
            assert frames[0][1]["after"] == 0
            assert "stream.replayed" not in [f["kind"] for _, f in frames]
    finally:
        await svc.stop()


async def test_native_reconnect_sees_every_event_exactly_once(tmp_path, monkeypatch):
    """An EventSource opened without `after`: live events, a drop, events while
    disconnected, then the automatic reconnect with Last-Event-ID."""
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.05)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        feeder = asyncio.create_task(emit_later(svc, 1, 2))
        before_drop = await collect(app, f"task_id={TASK}", svc.auth.token, has_note(2))
        await feeder
        assert notes(before_drop) == [1, 2]
        last_id = [sse_id for sse_id, f in before_drop if f.get("kind") == "task.note"][-1]

        await svc.bus.emit("task.note", task_id=TASK, n=3)          # while the browser is away
        feeder = asyncio.create_task(emit_later(svc, 4))
        after_drop = await collect(app, f"task_id={TASK}", svc.auth.token, has_note(4),
                                   last_event_id=last_id)
        await feeder
        assert notes(after_drop) == [3, 4], after_drop
        assert notes(before_drop) + notes(after_drop) == [1, 2, 3, 4], "no event lost, none twice"
    finally:
        await svc.stop()
