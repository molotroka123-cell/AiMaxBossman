"""/api/events/stream: reconnect with a cursor gets every missed event exactly once,
in order, then live events without duplicates; the stream ends when the client
goes away (no hung generator / leaked bus subscription).

The ASGI app is driven directly: httpx's ASGITransport buffers a whole response
and would never return from an endless stream."""
from __future__ import annotations

import asyncio
import json

from bcc import api as api_mod
from bcc.auth import HEADER

from .conftest import make_settings, start_app


async def drive(app, path, query, token, until, timeout=10.0):
    """Run one GET against the ASGI app; return the parsed data frames.
    `until(frames)` -> True triggers a client disconnect."""
    sent: list[bytes] = []
    disconnect = asyncio.Event()
    frames: list[dict] = []

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.body" and msg.get("body"):
            sent.append(msg["body"])
            for block in b"".join(sent).decode().split("\n\n"):
                for line in block.splitlines():
                    if line.startswith("data: "):
                        frame = json.loads(line[6:])
                        if frame not in frames:
                            frames.append(frame)
            if until(frames):
                disconnect.set()

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": [(b"host", b"test"), (HEADER.lower().encode(), token.encode())],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    await asyncio.wait_for(app(scope, receive, send), timeout)
    return frames


async def test_reconnect_with_cursor_replays_missed_events_once_then_goes_live(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.05)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        baseline = len(svc.bus._subscribers)                        # feature watchers hold some
        first = await svc.bus.emit("task.note", task_id=77, n=1)
        second = await svc.bus.emit("task.note", task_id=77, n=2)
        third = await svc.bus.emit("task.note", task_id=77, n=3)
        await svc.bus.emit("task.note", task_id=78, n=99)          # another task: never shown

        async def live_later():
            await asyncio.sleep(0.3)
            await svc.bus.emit("task.note", task_id=77, n=4)

        feeder = asyncio.create_task(live_later())
        frames = await drive(app, "/api/events/stream", f"task_id=77&after={first['seq']}",
                             svc.auth.token, until=lambda fs: any(f.get("n") == 4 for f in fs))
        await feeder
        notes = [f["n"] for f in frames if f.get("kind") == "task.note"]
        assert notes == [2, 3, 4], frames                          # missed ones once, in order, then live
        kinds = [f["kind"] for f in frames]
        assert kinds[0] == "stream.open" and "stream.replayed" in kinds
        # whether the live event (n=4) lands before or after the replay marker depends on machine speed;
        # what matters is that each missed event and the live one arrived exactly once
        assert kinds.count("task.note") == 3 and kinds.count("stream.replayed") == 1
        assert second["seq"] < third["seq"]
        await asyncio.sleep(0.2)
        assert len(svc.bus._subscribers) == baseline                # the closed stream unsubscribed
    finally:
        await svc.stop()
