"""Jeff 1.8: measured reply latency and incremental reply text (fakes only).

Adapter level: Ollama NDJSON stream with reasoning never shown. Runtime level: a
per-turn sink sees deltas, gets a reset when an attempt is discarded, and the
authoritative reply is still the returned value. Latency (time to first text,
total) is recorded per route and exposed through the heartbeat snapshot.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest

from bcc.pit import runtime as rt
from bcc.pit.latency import LatencyStats, ReplyMetrics
from bcc.pit.ollama_native import EmptyAnswer, OllamaNativeChatAdapter
from bcc.pit.reply_stream import EditPacer, TurnStream, reply_sink
from bcc.providers import ChatResult, ProviderError

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm

NATIVE = "http://127.0.0.1:11434/v1"


def _frames(*chunks, final=None, thinking=None):
    lines = []
    for chunk in chunks:
        body = {"model": "m", "message": {"role": "assistant", "content": chunk}, "done": False}
        if thinking:
            body["message"]["thinking"] = thinking
        lines.append(json.dumps(body, ensure_ascii=False))
    lines.append(json.dumps(final or {"model": "m", "message": {"content": ""}, "done": True,
                                       "done_reason": "stop", "eval_count": 9,
                                       "prompt_eval_count": 4}))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _adapter(handler):
    return OllamaNativeChatAdapter(NATIVE, transport=httpx.MockTransport(handler))


async def _collect(adapter, **kw):
    seen: list = []

    async def on_delta(text):
        seen.append(text)
    result = await adapter.chat("m", [{"role": "user", "content": "hi"}], on_delta=on_delta, **kw)
    return result, seen


def test_native_stream_sends_visible_text_incrementally_without_reasoning():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=_frames("Привет, ", "<thi", "nk>план</think>", "мир."),
                              headers={"content-type": "application/x-ndjson"})

    result, seen = asyncio.run(_collect(_adapter(handler)))
    assert bodies[0]["stream"] is True and bodies[0]["think"] is False
    assert seen == ["Привет, ", "мир."]
    assert result.text == "Привет, мир." and result.provider_meta["streamed"] is True
    assert result.provider_meta["ttft_ms"] >= 0 and result.tokens_out == 9


def test_native_stream_never_shows_the_thinking_field():
    def handler(request):
        return httpx.Response(200, content=_frames("Ответ.", thinking="скрытый ход мысли"))

    result, seen = asyncio.run(_collect(_adapter(handler)))
    assert "".join(seen) == "Ответ." and "мысли" not in result.text


def test_native_stream_empty_answer_is_an_empty_answer_and_shows_nothing():
    def handler(request):
        return httpx.Response(200, content=_frames(final={
            "model": "m", "message": {"content": ""}, "done": True, "eval_count": 1}))

    with pytest.raises(EmptyAnswer):
        asyncio.run(_collect(_adapter(handler)))


def test_native_stream_cut_after_text_discards_what_was_shown_and_fails():
    def handler(request):
        line = json.dumps({"message": {"content": "Начало"}, "done": False}, ensure_ascii=False)
        return httpx.Response(200, content=(line + "\n").encode("utf-8"))

    with pytest.raises(ProviderError):
        asyncio.run(_collect(_adapter(handler)))

    async def run():
        seen = []

        async def on_delta(text):
            seen.append(text)
        with pytest.raises(ProviderError):
            await _adapter(handler).chat("m", [{"role": "user", "content": "hi"}], on_delta=on_delta)
        return seen
    assert asyncio.run(run()) == ["Начало", None]


def test_native_stream_rejected_by_runner_falls_back_to_one_whole_call():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["stream"])
        if body["stream"]:
            return httpx.Response(400, json={"error": "no stream"})
        return httpx.Response(200, json={"model": "m", "message": {"content": "Целиком."},
                                         "done": True, "eval_count": 5})

    result, seen = asyncio.run(_collect(_adapter(handler)))
    assert calls == [True, False] and result.text == "Целиком." and seen == []


# -- runtime -----------------------------------------------------------------------------------
class StreamingLocal:
    """Local fake that emits deltas with a measurable delay before the first one."""

    def __init__(self, chunks=("Первая ", "часть ", "ответа."), lead=0.05, gap=0.02):
        self.chunks, self.lead, self.gap = chunks, lead, gap
        self.pricing = {"local:m": {"prompt": 0.0, "completion": 0.0}}
        self.calls = 0

    async def list_model_info(self):
        return [{"id": "local:m"}]

    async def chat(self, model, messages, **kw):
        self.calls += 1
        on_delta = kw.get("on_delta")
        await asyncio.sleep(self.lead)
        for chunk in self.chunks:
            if on_delta:
                await on_delta(chunk)
            await asyncio.sleep(self.gap)
        return ChatResult(text="".join(self.chunks), model=model, tokens_out=8)

    async def unload(self, model):
        return None


def _local_runtime(tmp_path, local):
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=(),
                                   local_url=NATIVE, local_models=("local:m",),
                                   local_chat_only=True)
    runtime = make_runtime(tmp_path, settings=settings)
    runtime.local_adapter = local
    runtime.local_settle_seconds = 0.0

    async def allowed():
        return True
    runtime.capacity_guard.local_allowed = allowed
    return runtime


def _turn(runtime, text="Привет", message_id=1, sink=None):
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))

    async def go():
        token = reply_sink.set(sink)
        try:
            return await runtime.handle(person, message(text, message_id=message_id))
        finally:
            reply_sink.reset(token)
    return asyncio.run(go())


def test_sink_receives_deltas_and_the_returned_reply_stays_authoritative(tmp_path):
    runtime = _local_runtime(tmp_path, StreamingLocal())
    shown = []

    async def sink(text):
        shown.append(text)
    try:
        answer = _turn(runtime, sink=sink)
        assert shown == ["Первая ", "часть ", "ответа."]
        assert answer == "Первая часть ответа."
    finally:
        asyncio.run(runtime.close())


def test_latency_is_measured_for_the_local_route_time_to_first_text_and_total(tmp_path):
    runtime = _local_runtime(tmp_path, StreamingLocal(lead=0.06, gap=0.03))
    try:
        _turn(runtime)
        snap = runtime.reply_metrics.snapshot()
        local = snap["local"]
        assert snap["replies"] == 1 and snap["streamed"] == 1
        ttft, total = local["ttft"]["last_ms"], local["total"]["last_ms"]
        assert ttft >= 50, ttft
        assert total >= ttft + 50, (ttft, total)
        assert local["ttft"]["p50_ms"] == ttft and local["total"]["p95_ms"] == total
        assert snap["remote"]["ttft"]["count"] == 0
    finally:
        asyncio.run(runtime.close())


def test_non_streaming_adapter_records_ttft_equal_to_total(tmp_path):
    class Whole(StreamingLocal):
        async def chat(self, model, messages, **kw):
            await asyncio.sleep(0.05)
            return ChatResult(text="Целый ответ.", model=model)
    runtime = _local_runtime(tmp_path, Whole())
    try:
        _turn(runtime)
        local = runtime.reply_metrics.snapshot()["local"]
        assert local["ttft"]["last_ms"] == local["total"]["last_ms"] >= 45
        assert runtime.reply_metrics.snapshot()["streamed"] == 0
    finally:
        asyncio.run(runtime.close())


def test_heartbeat_snapshot_exposes_reply_latency_and_route_status(tmp_path):
    runtime = _local_runtime(tmp_path, StreamingLocal(lead=0.01, gap=0.0))
    try:
        _turn(runtime)
        beat = runtime.heartbeat_snapshot("running")
        assert beat["reply_latency"]["local"]["total"]["count"] == 1
        assert beat["route"]["schema"] == "bossman.pit.model-route/1"
        assert beat["route"]["paid_routes_allowed"] is False
        assert "text" not in json.dumps(beat).lower().replace("context", "")
    finally:
        asyncio.run(runtime.close())


def test_incomplete_attempt_resets_the_preview_before_the_retry(tmp_path):
    class Truncated(StreamingLocal):
        async def chat(self, model, messages, **kw):
            self.calls += 1
            on_delta = kw.get("on_delta")
            if self.calls == 1:
                await on_delta("Обрывок фразы без конца слов")
                return ChatResult(text="Обрывок фразы без конца слов", finish="length", model=model)
            await on_delta("Полный ответ.")
            return ChatResult(text="Полный ответ.", model=model)
    runtime = _local_runtime(tmp_path, Truncated())
    events = []

    async def sink(text):
        events.append(text)
    try:
        answer = _turn(runtime, sink=sink)
        assert answer == "Полный ответ."
        assert events == ["Обрывок фразы без конца слов", None, "Полный ответ."]
    finally:
        asyncio.run(runtime.close())


def test_failed_turn_leaves_no_preview_behind(tmp_path):
    class Dies(StreamingLocal):
        async def chat(self, model, messages, **kw):
            await kw["on_delta"]("Частичный текст")
            raise ProviderError("оборвано", kind="network")
    runtime = _local_runtime(tmp_path, Dies())
    events = []

    async def sink(text):
        events.append(text)
    try:
        answer = _turn(runtime, sink=sink)
        assert answer == rt.PROVIDER_DOWN_RU
        assert events[-1] is None
    finally:
        asyncio.run(runtime.close())


def test_a_broken_sink_never_breaks_the_reply(tmp_path):
    runtime = _local_runtime(tmp_path, StreamingLocal(lead=0.0, gap=0.0))

    async def sink(text):
        raise RuntimeError("client went away")
    try:
        assert _turn(runtime, sink=sink) == "Первая часть ответа."
    finally:
        asyncio.run(runtime.close())


def test_stop_cancels_a_streaming_turn_promptly(tmp_path):
    runtime = _local_runtime(tmp_path, StreamingLocal(chunks=("а",) * 200, lead=0.0, gap=0.05))
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    shown = []

    async def sink(text):
        shown.append(text)

    async def go():
        token = reply_sink.set(sink)
        try:
            task = asyncio.create_task(runtime.handle(person, message("Привет", message_id=1)))
        finally:
            reply_sink.reset(token)
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        asyncio.run(asyncio.wait_for(go(), timeout=10))
        assert 0 < len(shown) < 200
    finally:
        asyncio.run(runtime.close())


# -- units -------------------------------------------------------------------------------------
def test_latency_stats_percentiles_and_failures():
    stats = LatencyStats(maxlen=5)
    for value in (10, 20, 30, 40, 50, 60):
        stats.add(value)
    stats.add(0, ok=False)
    snap = stats.snapshot()
    assert snap["count"] == 7 and snap["failures"] == 1 and snap["last_ms"] == 60
    assert snap["p50_ms"] == 40 and snap["p95_ms"] == 60
    assert LatencyStats().snapshot()["p50_ms"] is None
    metrics = ReplyMetrics()
    metrics.record("local", ttft_ms=5, total_ms=9, streamed=True)
    metrics.record("cloud", ttft_ms=7, total_ms=7, streamed=False)
    assert metrics.snapshot()["streamed"] == 1 and metrics.snapshot()["remote"]["ttft"]["last_ms"] == 7


def test_turn_stream_uses_the_injected_clock():
    ticks = iter([100.0, 100.25])
    stream = TurnStream(None, clock=lambda: next(ticks))
    asyncio.run(stream.on_delta("текст"))
    assert stream.ttft_ms == pytest.approx(250.0)


def test_edit_pacer_limits_rate_and_size():
    now = [0.0]
    pacer = EditPacer(interval=1.5, min_chars=10, clock=lambda: now[0])
    assert pacer.due(12)
    pacer.mark(12)
    now[0] = 0.5
    assert not pacer.due(50)
    now[0] = 2.0
    assert not pacer.due(15)
    assert pacer.due(30)
