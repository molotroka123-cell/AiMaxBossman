"""/api/events/stream: the keepalive clock counts from the last byte written to THIS client.

Events that the task filter drops (other tasks, system.metrics) used to restart the wait, so a quiet task stream was
never pinged while the bus stayed busy: the browser's watchdog then showed a false "connection lost" (chat UX run B2).
Driven on the ASGI app directly like test_events_stream_last_event_id.py (httpx would buffer an endless stream)."""
from __future__ import annotations

import asyncio

from bcc import api as api_mod
from bcc.auth import HEADER

from .conftest import make_settings, start_app

TASK = 77


async def raw_stream(app, query, token, stop_after_s):
    chunks: list[str] = []
    disconnect = asyncio.Event()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.body" and msg.get("body"):
            chunks.append(msg["body"].decode())

    path = "/api/events/stream"
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "root_path": "", "query_string": query.encode(),
             "headers": [(b"host", b"test"), (HEADER.lower().encode(), token.encode())],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    runner = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(stop_after_s)
    disconnect.set()
    await asyncio.wait_for(runner, 10)
    return "".join(chunks)


async def busy_other_task(svc, seconds):
    end = asyncio.get_running_loop().time() + seconds
    n = 0
    while asyncio.get_running_loop().time() < end:
        n += 1
        await svc.bus.emit("task.note", task_id=TASK + 1, n=n)        # another task: the filter drops it for this client
        await asyncio.sleep(0.03)


async def test_a_quiet_task_stream_is_pinged_while_other_tasks_keep_the_bus_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.25)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        feeder = asyncio.create_task(busy_other_task(svc, 1.4))
        body = await raw_stream(app, f"task_id={TASK}", svc.auth.token, 1.5)
        await feeder
        assert body.count(": keepalive") >= 2, body
        assert "\"n\"" not in body, "events of the other task must still be filtered out"
    finally:
        await svc.stop()


async def test_keepalive_is_not_sent_while_events_of_this_task_flow(tmp_path, monkeypatch):
    """Negative control: frames that ARE written restart the clock, so a live task stream gets no needless pings."""
    monkeypatch.setattr(api_mod, "STREAM_KEEPALIVE_S", 0.25)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async def own_events():
            for n in range(30):
                await svc.bus.emit("task.note", task_id=TASK, n=n)
                await asyncio.sleep(0.05)

        feeder = asyncio.create_task(own_events())
        body = await raw_stream(app, f"task_id={TASK}", svc.auth.token, 1.4)
        await feeder
        assert body.count("data: ") > 10
        assert ": keepalive" not in body, body
    finally:
        await svc.stop()
