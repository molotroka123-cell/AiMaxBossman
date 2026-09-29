"""Owner P1: the first answer tokens reach the run event stream BEFORE the model
finishes; STOP aborts the in-flight HTTP stream; the full answer is saved once;
a provider that cannot stream falls back honestly.

The model is a real local SSE server (raw asyncio socket, deltas with delays):
the test can hold the model mid-answer and look at what the owner would see.
"""
from __future__ import annotations

import asyncio
import json

from .conftest import client_for, make_settings, start_app, wait_for
from .helpers import make_stack

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}


def _frame(delta: dict, finish: str | None = None) -> bytes:
    body = {"id": "c1", "model": "local-7b",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return b"data: " + json.dumps(body).encode() + b"\n\n"


class FakeSseModel:
    """Local OpenAI-compatible endpoint. `gate` holds the model between the first
    deltas and the rest, so the test decides when 'the model finishes'."""

    def __init__(self, *, mode: str = "sse", gate: asyncio.Event | None = None,
                 parts: tuple[str, ...] = ("Привет", ", ", "мир")):
        self.mode, self.gate, self.parts = mode, gate, parts
        self.requests: list[dict] = []
        self.closed_early = asyncio.Event()
        self.open_streams = 0
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1"

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
            body = json.loads(await reader.readexactly(length) or b"{}")
            self.requests.append(body)
            if self.mode == "json" or not body.get("stream"):
                data = json.dumps({"model": "local-7b", "choices": [{
                    "message": {"role": "assistant", "content": "".join(self.parts)},
                    "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(data) + data)
                await writer.drain()
                return
            if self.mode == "reject_stream":
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            self.open_streams += 1
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Cache-Control: no-cache\r\nConnection: close\r\n\r\n")
            # reasoning must never reach the answer stream
            writer.write(_frame({"reasoning_content": "СЕКРЕТНОЕ-РАССУЖДЕНИЕ"}))
            writer.write(_frame({"content": self.parts[0]}))
            await writer.drain()
            if self.gate is not None:
                waiter = asyncio.ensure_future(self.gate.wait())
                eof = asyncio.ensure_future(reader.read(1))      # client hangs up -> EOF
                done, _ = await asyncio.wait({waiter, eof}, return_when=asyncio.FIRST_COMPLETED)
                for t in (waiter, eof):
                    if t not in done:
                        t.cancel()
                if eof in done:
                    self.open_streams -= 1
                    self.closed_early.set()
                    return
            for part in self.parts[1:]:
                writer.write(_frame({"content": part}))
                await writer.drain()
                await asyncio.sleep(0.01)
            writer.write(_frame({}, "stop"))
            writer.write(b"data: " + json.dumps({"choices": [], "usage": {
                "prompt_tokens": 5, "completion_tokens": 3}}).encode() + b"\n\n")
            writer.write(b"data: [DONE]\n\n")
            await writer.drain()
            self.open_streams -= 1
        except (ConnectionError, asyncio.IncompleteReadError):
            if self.open_streams:
                self.open_streams -= 1
            self.closed_early.set()
        finally:
            writer.close()


async def _stack(client, base_url: str) -> dict:
    prov = (await client.post("/api/providers", json={"name": "loc", "kind": "openai_compat",
                                                      "base_url": base_url})).json()
    model = (await client.post("/api/models", json={"provider_id": prov["id"], "name": "local-7b",
                                                    "alias": "local-7b"})).json()
    agent = (await client.post("/api/agents", json={"name": "a", "model_id": model["id"],
                                                    "max_steps": 1})).json()
    task = (await client.post("/api/tasks", json={"title": "t", "prompt": "привет", "agent_id": agent["id"],
                                                  "run_now": True})).json()["task"]
    return task


async def _events(client, tid: int, after: int = 0) -> list[dict]:
    return (await client.get(f"/api/tasks/{tid}/events", params={"after": after, "limit": 2000})).json()["events"]


async def test_first_delta_reaches_event_stream_before_model_finishes(tmp_path):
    gate = asyncio.Event()
    model = FakeSseModel(gate=gate)
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            task = await _stack(client, base)
            tid = task["id"]

            async def first_delta():
                evs = [e for e in await _events(client, tid) if e["kind"] == "run.answer_delta"]
                return evs or None

            deltas = await wait_for(first_delta, timeout=10)        # model is STILL holding the gate
            assert not gate.is_set()
            assert "".join(e["text"] for e in deltas) == "Привет"
            evs = await _events(client, tid)
            assert "run.assistant_message" not in [e["kind"] for e in evs]     # answer not final yet
            assert "СЕКРЕТНОЕ" not in json.dumps(evs, ensure_ascii=False)      # reasoning never shown
            cursor = evs[-1]["seq"]

            gate.set()

            async def done():
                t = (await client.get(f"/api/tasks/{tid}")).json()["task"]
                return t["status"] == "completed"
            await wait_for(done, timeout=15)
            evs = await _events(client, tid)
            answers = [e for e in evs if e["kind"] == "run.answer_delta"]
            assert "".join(e["text"] for e in answers) == "Привет, мир"
            seqs = [e["seq"] for e in evs]
            assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
            assert [e["idx"] for e in answers] == list(range(len(answers)))     # append-only, ordered
            finals = [e for e in evs if e["kind"] == "run.assistant_message"]
            assert len(finals) == 1 and finals[0]["text"] == "Привет, мир" and finals[0]["streamed"] is True
            assert not [e for e in evs if e["kind"] == "run.assistant_delta"]  # no double delivery
            # reconnect: replay after a cursor gives exactly the tail, no duplicates
            all_now = await _events(client, tid)
            tail = await _events(client, tid, after=cursor)
            assert [e["seq"] for e in tail] == [e["seq"] for e in all_now if e["seq"] > cursor]
            assert not {e["seq"] for e in tail} & {e["seq"] for e in all_now if e["seq"] <= cursor}
            assert len(model.requests) == 1 and model.requests[0].get("stream") is True
    finally:
        await svc.stop()
        await model.stop()


async def test_stop_aborts_in_flight_stream_and_closes_connection(tmp_path):
    gate = asyncio.Event()                         # never opened: the model hangs mid-answer
    model = FakeSseModel(gate=gate)
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            task = await _stack(client, base)
            tid = task["id"]

            async def first_delta():
                return [e for e in await _events(client, tid) if e["kind"] == "run.answer_delta"] or None
            await wait_for(first_delta, timeout=10)
            assert (await client.post(f"/api/tasks/{tid}/stop")).status_code == 200

            async def stopped():
                return (await client.get(f"/api/tasks/{tid}")).json()["task"]["status"] == "stopped"
            await wait_for(stopped, timeout=10)
            await asyncio.wait_for(model.closed_early.wait(), timeout=5)        # socket closed by client
            assert model.open_streams == 0
            evs = await _events(client, tid)
            assert not [e for e in evs if e["kind"] == "run.assistant_message"]  # nothing saved as final
    finally:
        await svc.stop()
        await model.stop()


async def test_provider_that_rejects_stream_falls_back_to_whole_answer(tmp_path):
    model = FakeSseModel(mode="reject_stream")
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            task = await _stack(client, base)
            tid = task["id"]

            async def done():
                return (await client.get(f"/api/tasks/{tid}")).json()["task"]["status"] == "completed"
            await wait_for(done, timeout=15)
            evs = await _events(client, tid)
            assert not [e for e in evs if e["kind"] == "run.answer_delta"]
            finals = [e for e in evs if e["kind"] == "run.assistant_message"]
            assert len(finals) == 1 and finals[0]["text"] == "Привет, мир"
            assert not finals[0].get("streamed")
            assert [bool(r.get("stream")) for r in model.requests] == [True, False]
    finally:
        await svc.stop()
        await model.stop()


async def test_json_reply_to_stream_request_is_used_whole(tmp_path):
    model = FakeSseModel(mode="json")
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            tid = (await _stack(client, base))["id"]

            async def done():
                return (await client.get(f"/api/tasks/{tid}")).json()["task"]["status"] == "completed"
            await wait_for(done, timeout=15)
            evs = await _events(client, tid)
            assert not [e for e in evs if e["kind"] == "run.answer_delta"]
            assert [e["text"] for e in evs if e["kind"] == "run.assistant_message"] == ["Привет, мир"]
            assert len(model.requests) == 1                    # no second request needed
    finally:
        await svc.stop()
        await model.stop()


# --- pure pieces --------------------------------------------------------------

def _lines(*frames: object) -> list[str]:
    out: list[str] = []
    for f in frames:
        out += ["data: " + (f if isinstance(f, str) else json.dumps(f)), ""]
    return out


async def _aiter(lines):
    for line in lines:
        yield line


async def test_read_chat_stream_assembles_tool_calls_and_hides_reasoning():
    from bcc.streaming import read_chat_stream
    seen: list[str] = []

    async def on_text(t: str) -> None:
        seen.append(t)

    frames = [
        {"choices": [{"delta": {"reasoning": "тайна"}}]},
        {"choices": [{"delta": {"content": "Ищу <thi"}}]},
        {"choices": [{"delta": {"content": "nk>скрыто</think>файл"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read", "arguments": '{"p":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"a"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
        "[DONE]",
    ]
    cs = await read_chat_stream(_aiter(_lines(*frames)), on_text)
    assert "".join(seen) == "Ищу файл" and "тайна" not in "".join(seen)
    msg = cs.message
    assert msg["reasoning_content"] == "тайна"
    assert msg["tool_calls"][0]["function"] == {"name": "read", "arguments": '{"p":"a"}'}
    assert cs.usage == {"prompt_tokens": 4, "completion_tokens": 2} and cs.terminated


async def test_stream_cut_mid_answer_discards_shown_text_and_fails_the_call():
    import httpx

    from bcc.providers import OpenAICompatAdapter, ProviderError

    calls: list = []

    async def on_delta(t):
        calls.append(t)

    def handler(request: httpx.Request) -> httpx.Response:
        body = f"data: {json.dumps({'choices': [{'delta': {'content': 'half'}}]})}\n\n"   # no finish, no [DONE]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:9/v1", transport=httpx.MockTransport(handler))
    try:
        await adapter.chat("m", [{"role": "user", "content": "x"}], on_delta=on_delta)
    except ProviderError as exc:
        assert exc.kind == "network"
    else:
        raise AssertionError("a truncated stream must not become an answer")
    assert calls == ["half", None]                 # shown, then told to discard


def test_human_view_prints_live_lines_once_and_skips_final_duplicate():
    import io

    from bcc.terminal_cli.console import make_console
    from bcc.terminal_cli.human import View
    from bcc.terminal_cli.records import normalize
    buf = io.StringIO()
    view = View(make_console(stream=buf, plain=True, width=160), plain=True)
    for text in ("Первая строка\nВто", "рая", " строка\n"):
        for rec in normalize({"kind": "run.answer_delta", "task_id": 1, "run_id": 1, "step": 1,
                              "text": text, "attempt": 0, "idx": 0}):
            view.on_record(rec)
    for rec in normalize({"kind": "run.assistant_message", "task_id": 1, "run_id": 1, "step": 1,
                          "text": "Первая строка\nВторая строка", "streamed": True}):
        view.on_record(rec)
    out = buf.getvalue()
    assert out.count("Первая строка") == 1 and out.count("Вторая строка") == 1
