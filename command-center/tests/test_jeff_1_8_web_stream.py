"""Jeff 1.8: incremental replies in the Jeff window (SSE), STOP kept, one saved reply."""
from __future__ import annotations

import asyncio
import json
import threading
import time

from bcc.pit import runtime as rt
from bcc.pit import web
from bcc.providers import ChatResult

from .test_pit_web import H, client_for, make_app, signup, RecordingAdapter


class StreamingAdapter(RecordingAdapter):
    def __init__(self, chunks=("Пер", "вая ", "часть."), gap=0.0, discard_first=False):
        super().__init__("".join(chunks))
        self.chunks, self.gap, self.discard_first = chunks, gap, discard_first

    async def chat(self, model, messages, **kw):
        self.calls.append((model, messages))
        on_delta = kw.get("on_delta")
        if self.discard_first and len(self.calls) == 1:
            await on_delta("Обрывок, который надо выбросить")
            return ChatResult(text="Обрывок, который надо выбросить", finish="length", model=model)
        for chunk in self.chunks:
            if on_delta:
                await on_delta(chunk)
            if self.gap:
                await asyncio.sleep(self.gap)
        return ChatResult(text="".join(self.chunks), model=model, tokens_out=5)


def events(response):
    out, name = [], None
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((name, json.loads(line.split(":", 1)[1])))
    return out


def stream(client, text, **extra):
    return client.stream("POST", "/api/jeff/chat/stream", json={"text": text, **extra}, headers=H)


def test_sse_delivers_deltas_then_one_final_reply_and_saves_it_once(tmp_path):
    adapter = StreamingAdapter()
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        with stream(c, "Привет") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            got = events(response)
        history = c.get("/api/jeff/history").json()["messages"]
    kinds = [name for name, _ in got]
    assert kinds == ["delta", "delta", "delta", "final"]
    assert "".join(data["t"] for name, data in got if name == "delta") == "Первая часть."
    final = got[-1][1]
    assert final["reply"] == "Первая часть." and final["stopped"] is False and final["disclosure"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["text"] == "Первая часть."
    assert len(adapter.calls) == 1


def test_sse_reset_event_drops_a_discarded_attempt(tmp_path):
    adapter = StreamingAdapter(chunks=("Полный ответ.",), discard_first=True)
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        with stream(c, "Привет") as response:
            got = events(response)
    assert [name for name, _ in got] == ["delta", "reset", "delta", "final"]
    assert got[-1][1]["reply"] == "Полный ответ."


def test_sse_without_streaming_adapter_still_ends_with_the_final_reply(tmp_path):
    app, _ = make_app(tmp_path, RecordingAdapter("Целиком."))
    with client_for(app) as c:
        signup(c)
        with stream(c, "Привет") as response:
            got = events(response)
    assert [name for name, _ in got][-1] == "final" and got[-1][1]["reply"] == "Целиком."


def test_stop_ends_a_streaming_reply_quickly_and_saves_no_assistant_message(tmp_path):
    adapter = StreamingAdapter(chunks=("а",) * 400, gap=0.05)
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        box = {}

        def run():
            with stream(c, "расскажи длинную историю") as response:
                box["events"] = events(response)
        worker = threading.Thread(target=run)
        worker.start()
        for _ in range(100):
            if adapter.calls:
                break
            time.sleep(0.05)
        time.sleep(0.3)
        started = time.monotonic()
        assert c.post("/api/jeff/stop", json={}, headers=H).json()["cancelled"] is True
        worker.join(timeout=5)
        assert time.monotonic() - started < 3
        history = c.get("/api/jeff/history").json()["messages"]
    final = box["events"][-1]
    assert final[0] == "final" and final[1]["stopped"] is True and final[1]["reply"] == web.STOPPED_RU
    assert any(name == "delta" for name, _ in box["events"])
    assert all(m["role"] != "assistant" or "аа" not in m["text"] for m in history)


def test_sse_guards_match_the_plain_chat_endpoint(tmp_path):
    app, _ = make_app(tmp_path, StreamingAdapter())
    with client_for(app) as c:
        anon = c.post("/api/jeff/chat/stream", json={"text": "x"}, headers=H)
        assert anon.status_code == 401
        signup(c)
        assert c.post("/api/jeff/chat/stream", json={"text": "x"}).status_code == 403   # no CSRF header
        assert c.post("/api/jeff/chat/stream", json={"text": "  "}, headers=H).status_code == 400
        with stream(c, "/memory", via="voice") as response:
            got = events(response)
    assert [name for name, _ in got] == ["final"] and got[0][1]["reply"] == rt.FORBIDDEN_REPLY_RU


def test_health_serves_reply_latency_and_route_status(tmp_path):
    app, _ = make_app(tmp_path, StreamingAdapter())
    with client_for(app) as c:
        signup(c)
        with stream(c, "Привет") as response:
            events(response)
        health = c.get("/api/jeff/health").json()
    beat = health["heartbeat"]
    assert beat["reply_latency"]["replies"] == 1
    assert beat["route"]["schema"] == "bossman.pit.model-route/1"
    assert "latency" in beat["stt"] and "latency" in beat["tts"]


def test_jeff_window_streams_replies_and_keeps_stop_and_a_plain_fallback():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "ui" / "jeff.js").read_text(encoding="utf-8")
    assert "/api/jeff/chat/stream" in js and "chatStream(" in js
    assert "await call('/api/jeff/chat'" in js, "plain endpoint stays as the fallback"
    assert "chatAbort.abort()" in js and "/api/jeff/stop" in js
