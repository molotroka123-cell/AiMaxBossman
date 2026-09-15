"""Actual gateway adapters with synthetic wire data, never live-provider evidence."""
from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from bossman.gateway.backends import AnthropicBackend, BackendError, build_backend
from bossman.gateway.config import AVAILABLE_PROVIDERS, load_provider_config
from bossman.gateway.prompt_cache import SSEUsageCollector, extract_cache_usage


def _request():
    return {
        "model": "claude-contract",
        "messages": [
            {"role": "system", "content": "Use verified files."},
            {"role": "user", "content": "Read safe.txt"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"safe.txt"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "observed bytes"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "read_file", "description": "Read an allowed file.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"],
                "additionalProperties": False}, "strict": True}}],
    }


def _reply(**overrides):
    return {"id": "msg_1", "model": "claude-contract", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "observed"}],
            "usage": {"input_tokens": 10, "output_tokens": 4}, **overrides}


def _event(kind, **fields):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields},
                                                        ensure_ascii=False) + "\n\n").encode()


def _events():
    return [
        _event("message_start", message=_reply(content=[], stop_reason=None,
               usage={"input_tokens": 10, "output_tokens": 1,
                      "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2})),
        _event("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        _event("content_block_delta", index=0, delta={"type": "text_delta", "text": "Привет"}),
        _event("content_block_stop", index=0),
        _event("content_block_start", index=1, content_block={
            "type": "tool_use", "id": "call_1", "name": "read_file", "input": {}}),
        _event("content_block_delta", index=1, delta={
            "type": "input_json_delta", "partial_json": '{"path":'}),
        _event("content_block_delta", index=1, delta={
            "type": "input_json_delta", "partial_json": '"safe.txt"}'}),
        _event("content_block_stop", index=1),
        _event("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 7}),
        _event("message_stop"),
    ]


class Wire(httpx.AsyncByteStream):
    def __init__(self, chunks, *, repeat=False, delay=0):
        self.chunks, self.repeat, self.delay = chunks, repeat, delay
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        while True:
            for chunk in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                self.reads += 1
                yield chunk
            if not self.repeat:
                return

    async def aclose(self):
        self.closed = True


def _backend(wire, **options):
    async def handler(request):
        assert request.url.path == "/v1/messages"
        return httpx.Response(200, stream=wire, headers={"content-type": "text/event-stream"})
    return AnthropicBackend.from_env(api_key="fixture-only",
                                    transport=httpx.MockTransport(handler), **options)


def _frames(chunks):
    return [json.loads(line[5:]) for line in b"".join(chunks).splitlines()
            if line.startswith(b"data:") and line[5:].strip() != b"[DONE]"]


def test_tools_and_results_survive_both_protocol_boundaries():
    request = _request()
    original = copy.deepcopy(request)
    native = AnthropicBackend.to_anthropic_payload(request)
    assert native["tools"][0] == {
        "name": "read_file", "description": "Read an allowed file.",
        "input_schema": request["tools"][0]["function"]["parameters"], "strict": True}
    assert native["messages"][1]["content"] == [{"type": "tool_use", "id": "call_1",
                                                 "name": "read_file", "input": {"path": "safe.txt"}}]
    assert native["messages"][2] == {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "call_1", "content": "observed bytes"}]}
    assert request == original
    response = AnthropicBackend.to_openai_response(_reply(stop_reason="tool_use", content=[
        {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "safe.txt"}}]))
    call = response["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "call_1" and call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "safe.txt"}
    assert response["choices"][0]["finish_reason"] == "tool_calls"


def test_json_schema_and_explicit_tool_choice_are_translated_not_discarded():
    request = _request()
    request.update(tool_choice={"type": "function", "function": {"name": "read_file"}},
                   parallel_tool_calls=False,
                   response_format={"type": "json_schema", "json_schema": {
                       "name": "result", "strict": True, "schema": {"type": "object",
                       "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                       "additionalProperties": False}}})
    native = AnthropicBackend.to_anthropic_payload(request)
    assert native["tool_choice"] == {"type": "tool", "name": "read_file", "disable_parallel_tool_use": True}
    assert native["output_config"]["format"] == {"type": "json_schema",
        "schema": request["response_format"]["json_schema"]["schema"]}
    assert "response_format" not in native and "parallel_tool_calls" not in native


@pytest.mark.parametrize("arguments", ['{broken', '[]', 'null', '{"v":NaN}'])
def test_malformed_tool_arguments_are_refused_before_network(arguments):
    request = _request()
    request["messages"][2]["tool_calls"][0]["function"]["arguments"] = arguments
    with pytest.raises(ValueError):
        AnthropicBackend.to_anthropic_payload(request)


def test_cache_usage_includes_all_input_and_preserves_cache_economics():
    response = AnthropicBackend.to_openai_response(_reply(usage={
        "input_tokens": 10, "output_tokens": 4,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2}))
    usage = extract_cache_usage(response)
    assert usage["prompt_tokens"] == 17
    assert usage["completion_tokens"] == 4
    assert usage["cache_read_tokens"] == 5 and usage["cache_write_tokens"] == 2
    assert usage["fresh_input_tokens"] == 10
    assert response["usage"]["total_tokens"] == 21


@pytest.mark.parametrize("width", [1, 7, 65536])
async def test_native_stream_fragments_become_openai_tools_text_usage_and_done(width):
    data = b"".join(_events())
    wire = Wire([data[i:i+width] for i in range(0, len(data), width)])
    backend = _backend(wire)
    try:
        chunks = [c async for c in backend.stream_request("/v1/chat/completions", _request())]
    finally:
        await backend.close()
    frames = _frames(chunks)
    deltas = [choice["delta"] for f in frames for choice in f.get("choices", [])]
    assert "".join(d.get("content", "") for d in deltas) == "Привет"
    calls = [call for d in deltas for call in d.get("tool_calls", [])]
    assert calls[0]["id"] == "call_1"
    assert {c["index"] for c in calls} == {0}
    assert json.loads("".join(c["function"].get("arguments", "") for c in calls)) == {"path": "safe.txt"}
    assert b"".join(chunks).endswith(b"data: [DONE]\n\n")
    usage_frames = [f for f in frames if "usage" in f]
    assert len(usage_frames) == 1, "interim output_tokens=1 is not final billable usage"
    collector = SSEUsageCollector()
    for chunk in chunks:
        collector.feed(chunk)
    collector.finish()
    assert extract_cache_usage(collector.body)["prompt_tokens"] == 17
    assert extract_cache_usage(collector.body)["completion_tokens"] == 7
    assert wire.closed


@pytest.mark.parametrize("bad", [
    [],
    _events()[:-1],
    [_event("message_start", message=_reply(content=[])), b"data: {bad}\n\n"],
    [_event("message_start", message=_reply(content=[])), _event("error", error={"type": "overloaded_error"})],
    [_event("message_start", message=_reply(content=[])), _event("message_stop")],
])
async def test_bad_native_stream_never_finishes_successfully(bad):
    wire = Wire(bad)
    backend = _backend(wire)
    chunks = []
    try:
        with pytest.raises(BackendError):
            async for chunk in backend.stream_request("/v1/chat/completions", _request()):
                chunks.append(chunk)
        assert backend.breaker.consecutive_failures == 1
        assert not any(b"[DONE]" in c for c in chunks)
        assert not any("usage" in f for f in _frames(chunks))
    finally:
        await backend.close()
    assert wire.closed


async def test_cancellation_closes_native_connection_without_fake_finish():
    wire = Wire(_events(), repeat=True)
    backend = _backend(wire)
    stream = backend.stream_request("/v1/chat/completions", _request())
    first = await anext(stream)
    assert b"choices" in first
    await stream.aclose()
    assert wire.closed
    assert wire.reads == 1
    assert backend.breaker.consecutive_failures == 0
    await backend.close()


async def test_infinite_ping_stream_has_a_total_deadline_and_closes():
    wire = Wire([_event("ping")], repeat=True, delay=0.005)
    backend = _backend(wire, timeout_seconds=0.025)
    try:
        with pytest.raises(BackendError, match="timed out"):
            await asyncio.wait_for(anext(backend.stream_request("/v1/chat/completions", _request())), timeout=0.5)
    finally:
        await backend.close()
    assert wire.closed and wire.reads < 50


async def test_malformed_native_json_trips_breaker_without_resetting_failure_history():
    async def handler(request):
        return httpx.Response(200, json=_reply(content=[]))
    backend = AnthropicBackend.from_env(api_key="fixture-only", circuit_failure_threshold=2,
                                        transport=httpx.MockTransport(handler))
    try:
        for _ in range(2):
            with pytest.raises(BackendError):
                await backend.json_request("/v1/chat/completions", _request())
        assert backend.breaker.state == "open"
    finally:
        await backend.close()


@pytest.mark.parametrize("provider", sorted(AVAILABLE_PROVIDERS))
async def test_each_configured_provider_preserves_a_tool_response(provider):
    seen = []
    async def handler(request):
        seen.append(json.loads(request.content))
        body = (_reply(stop_reason="tool_use", content=[{"type": "tool_use", "id": "t1",
                 "name": "read_file", "input": {"path": "safe.txt"}}]) if provider == "anthropic" else {
                 "choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "t1",
                 "type": "function", "function": {"name": "read_file", "arguments": '{"path":"safe.txt"}'}}]}}]})
        return httpx.Response(200, json=body)
    backend = build_backend(load_provider_config(provider, api_key="fixture-only"),
                            transport=httpx.MockTransport(handler))
    try:
        response, _ = await backend.json_request("/v1/chat/completions", _request())
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "read_file"
        assert json.loads(call["function"]["arguments"]) == {"path": "safe.txt"}
        assert backend.breaker.state == "closed"
        assert seen[0]["tools"]
    finally:
        await backend.close()


@pytest.mark.parametrize("status,failover", [(400, False), (401, False), (403, False),
                                           (404, False), (429, True), (503, True)])
async def test_native_http_refusal_preserves_failure_class(status, failover):
    async def handler(request):
        return httpx.Response(status, json={"error": {"type": "fixture"}})
    backend = AnthropicBackend.from_env(api_key="fixture-only", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendError) as failed:
            await anext(backend.stream_request("/v1/chat/completions", _request()))
        assert failed.value.status_code == status
        assert failed.value.failover is failover
        assert backend.breaker.consecutive_failures == int(failover)
    finally:
        await backend.close()


async def test_wire_volume_bound_stops_an_infinite_upstream(monkeypatch):
    import bossman.gateway.backends as transport
    monkeypatch.setattr(transport, "MAX_STREAM_BYTES", 200)
    wire = Wire([_event("ping")], repeat=True)
    backend = _backend(wire)
    try:
        with pytest.raises(BackendError, match="size limit"):
            await asyncio.wait_for(anext(backend.stream_request("/v1/chat/completions", _request())), timeout=1)
        assert backend.breaker.consecutive_failures == 1
    finally:
        await backend.close()
    assert wire.closed and wire.reads <= 10


async def test_oversized_unterminated_event_does_not_accumulate_forever(monkeypatch):
    import bossman.gateway.anthropic_protocol as protocol
    monkeypatch.setattr(protocol, "MAX_EVENT_BYTES", 64)
    wire = Wire([b"data: " + b"a" * 40], repeat=True)
    backend = _backend(wire)
    try:
        with pytest.raises(BackendError, match="size limit"):
            await anext(backend.stream_request("/v1/chat/completions", _request()))
    finally:
        await backend.close()
    assert wire.closed and wire.reads == 2


async def test_ping_event_count_is_bounded_independently_of_wire_size(monkeypatch):
    import bossman.gateway.anthropic_protocol as protocol
    monkeypatch.setattr(protocol, "MAX_EVENTS", 2)
    wire = Wire([_event("ping")], repeat=True)
    backend = _backend(wire)
    try:
        with pytest.raises(BackendError, match="event limit"):
            await anext(backend.stream_request("/v1/chat/completions", _request()))
    finally:
        await backend.close()
    assert wire.closed and wire.reads == 3


async def test_successful_native_recovery_closes_half_open_breaker():
    wire = Wire(_events(), repeat=True)
    backend = _backend(wire, circuit_failure_threshold=1, circuit_cooldown_seconds=0)
    backend.breaker.record_failure("fixture outage")
    assert backend.breaker.allow_attempt()
    try:
        chunks = [c async for c in backend.stream_request("/v1/chat/completions", _request())]
        assert chunks[-1] == b"data: [DONE]\n\n"
        assert backend.breaker.state == "closed"
        assert wire.reads == len(_events()), "do not drain an upstream after native message_stop"
    finally:
        await backend.close()
    assert wire.closed


def _gateway(handler):
    from bossman.gateway.app import create_gateway_app
    from bossman.gateway.config import AliasConfig, ClientConfig, GatewayConfig, ModelTarget
    from bossman.gateway.router import ModelRouter
    cfg = GatewayConfig(backends={"anthropic": load_provider_config("anthropic", api_key="fixture-only")},
        aliases={"selected": AliasConfig("selected", [ModelTarget("anthropic", "claude-contract", 10, {"text"},
            price_usd_per_million_input_tokens="1", price_usd_per_million_output_tokens="2")])},
        clients={"owner": ClientConfig("owner", key="fixture-token")})
    backend = build_backend(cfg.backends["anthropic"], transport=httpx.MockTransport(handler))
    return create_gateway_app(cfg, router=ModelRouter(cfg, {"anthropic": backend})), backend


@pytest.fixture
def gateway_budget_store(tmp_path, monkeypatch):
    import bossman.cost_control.runtime as runtime
    from bossman.cost_control.governor import CostGovernor
    from bossman.cost_control.models import BudgetPolicy, BudgetScope
    from bossman.cost_control.store import SQLiteBudgetStore
    store = SQLiteBudgetStore(tmp_path / "gateway-budget.db")
    monkeypatch.setattr(runtime, "STORE", store)
    events = []
    monkeypatch.setattr(runtime, "GOVERNOR", CostGovernor(
        store, lambda kind, **data: events.append({"kind": kind, **data})))
    store.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("10")))
    return store


AUTH = {"authorization": "Bearer fixture-token", "x-bossman-cloud-allowed": "1"}


@pytest.mark.parametrize("stream", [False, True])
async def test_actual_gateway_native_usage_settles_cost_once(gateway_budget_store, stream):
    async def handler(request):
        if stream:
            return httpx.Response(200, stream=Wire(_events()))
        return httpx.Response(200, json=_reply(usage={"input_tokens": 17, "output_tokens": 7}))
    app, backend = _gateway(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
            result = await client.post("/v1/chat/completions", headers=AUTH,
                json={"model": "selected", "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 200, "stream": stream})
            assert result.status_code == 200
        snapshot = gateway_budget_store.snapshots()[0]
        assert Decimal(snapshot["reserved_usd"]) == 0
        # No cache-discount price configured: all 17 prompt tokens count at $1/M.
        assert Decimal(snapshot["spent_usd"]) == Decimal("0.000031")
    finally:
        await backend.close()


@pytest.mark.parametrize("stream", [False, True])
async def test_corrupt_successful_upstream_is_not_refunded_as_free(gateway_budget_store, stream):
    async def handler(request):
        return (httpx.Response(200, stream=Wire([])) if stream
                else httpx.Response(200, json=_reply(content=[])))
    app, backend = _gateway(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
            result = await client.post("/v1/chat/completions", headers=AUTH,
                json={"model": "selected", "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 200, "stream": stream})
            assert ("error" in result.text) if stream else result.status_code == 502
        snapshot = gateway_budget_store.snapshots()[0]
        assert Decimal(snapshot["reserved_usd"]) == 0
        assert Decimal(snapshot["spent_usd"]) >= Decimal("0.0004"), "commit the unmeasured 200-token bound"
    finally:
        await backend.close()


async def test_gateway_partial_native_stream_does_not_settle_interim_usage(gateway_budget_store):
    wire = Wire(_events()[:3])
    async def handler(request):
        return httpx.Response(200, stream=wire)
    app, backend = _gateway(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
            result = await client.post("/v1/chat/completions", headers=AUTH,
                json={"model": "selected", "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 200, "stream": True})
        assert "error" in result.text and "[DONE]" not in result.text
        snapshot = gateway_budget_store.snapshots()[0]
        assert Decimal(snapshot["reserved_usd"]) == 0
        assert Decimal(snapshot["spent_usd"]) >= Decimal("0.0004")
        assert wire.closed and backend.semaphore._value == backend.config.max_concurrency
    finally:
        await backend.close()


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
async def test_actual_gateway_cancel_releases_stream_and_semaphore_but_not_spent_bound(gateway_budget_store, spec_version):
    wire = Wire(_events(), repeat=True)
    async def handler(request):
        return httpx.Response(200, stream=wire)
    app, backend = _gateway(handler)
    payload = json.dumps({"model": "selected", "messages": [{"role": "user", "content": "hi"}],
                          "max_tokens": 200, "stream": True}).encode()
    sent_request, first_body = False, asyncio.Event()
    frames = []
    never = asyncio.Event()

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            frames.append(message["body"])
            first_body.set()
            await never.wait()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
             "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
             "query_string": b"", "root_path": "", "server": ("gw", 80),
             "client": ("127.0.0.1", 1000),
             "headers": [(key.encode(), value.encode()) for key, value in AUTH.items()]
                        + [(b"content-type", b"application/json")],
    }
    serving = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(first_body.wait(), timeout=2)
        serving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serving
        assert not any(b"[DONE]" in frame for frame in frames)
        assert wire.closed
        assert backend.semaphore._value == backend.config.max_concurrency
        snapshot = gateway_budget_store.snapshots()[0]
        assert Decimal(snapshot["reserved_usd"]) == 0
        assert Decimal(snapshot["spent_usd"]) >= Decimal("0.0004")
    finally:
        if not serving.done():
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        await backend.close()


@pytest.mark.parametrize("counter", [None, -1, float("nan"), float("inf"), True, "7"])
def test_invalid_native_usage_is_not_normalized_to_zero(counter):
    with pytest.raises(ValueError):
        AnthropicBackend.to_openai_response(_reply(usage={"input_tokens": 10, "output_tokens": counter}))


@pytest.mark.parametrize("reason,expected", [("end_turn", "stop"), ("max_tokens", "length"),
                                            ("refusal", "content_filter")])
async def test_native_terminal_class_survives_translation(reason, expected):
    events = _events()[:4] + [_event("message_delta", delta={"stop_reason": reason},
                                    usage={"output_tokens": 4}), _event("message_stop")]
    backend = _backend(Wire(events))
    try:
        chunks = [c async for c in backend.stream_request("/v1/chat/completions", _request())]
        assert _frames(chunks)[-1]["choices"][0]["finish_reason"] == expected
    finally:
        await backend.close()


async def test_regressive_usage_and_malformed_tool_json_never_issue_finish():
    variants = [
        _events()[:-1] + [_event("message_delta", delta={}, usage={"output_tokens": 1}), _event("message_stop")],
        _events()[:5] + [_event("content_block_delta", index=1,
            delta={"type": "input_json_delta", "partial_json": "[broken"}), *_events()[7:]],
    ]
    for events in variants:
        wire = Wire(events)
        backend = _backend(wire)
        chunks = []
        try:
            with pytest.raises(BackendError):
                async for chunk in backend.stream_request("/v1/chat/completions", _request()):
                    chunks.append(chunk)
            assert not any(b"[DONE]" in c for c in chunks)
        finally:
            await backend.close()
        assert wire.closed
