from __future__ import annotations

import json
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from bossman.cost_control.pricing import cache_aware_actual_usd
from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget
from bossman.gateway.prompt_cache import (
    extract_cache_usage,
    prepare_provider_payload,
    stable_session_id,
)
from bossman.gateway.router import ModelRouter


SECRET = "provider-secret-canary-value"


def _prepare(messages, *, ttl="5m", session=None, provider="openrouter"):
    payload = {
        "model": "anthropic/claude-opus-5",
        "tools": [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        "messages": messages,
    }
    return prepare_provider_payload(
        payload, provider_kind=provider, provider_model=payload["model"],
        session_id=session or stable_session_id("coder", "run-1"), requested_ttl=ttl,
    )


def test_cache_stable_and_different_session_id():
    first = stable_session_id("coder", "task-1")
    assert first == stable_session_id("coder", "task-1")
    assert first != stable_session_id("coder", "task-2")
    assert len(first) <= 256


def test_no_secret_in_session_id():
    session_id = stable_session_id("coder", SECRET, "conversation")
    assert SECRET not in session_id
    assert "secret-canary" not in session_id


def test_anthropic_cache_control_5m_and_1h():
    messages = [{"role": "system", "content": "stable policy"},
                {"role": "user", "content": "latest task"}]
    five, five_meta = _prepare(messages, ttl="5m")
    one, one_meta = _prepare(messages, ttl="1h")
    assert five["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert one["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral", "ttl": "1h"}
    assert five_meta["ttl"] == "5m"
    assert one_meta["ttl"] == "1h"


def test_stable_prefix_dynamic_suffix_does_not_destroy_prefix():
    first, first_meta = _prepare([
        {"role": "system", "content": "system + security + stable architecture"},
        {"role": "user", "content": "task turn one"},
    ])
    second, second_meta = _prepare([
        {"role": "system", "content": "system + security + stable architecture"},
        {"role": "user", "content": "new task state, diff, diagnostics"},
    ])
    assert first_meta["prefix_hash"] == second_meta["prefix_hash"]
    assert first_meta["prefix_tokens"] == second_meta["prefix_tokens"]
    assert first["messages"][0]["content"][0]["text"] == second["messages"][0]["content"][0]["text"]


def test_unsupported_provider_fail_open_unchanged():
    source = {"model": "local", "messages": [{"role": "user", "content": "same"}]}
    output, meta = prepare_provider_payload(
        source, provider_kind="openai", provider_model="local",
        session_id=stable_session_id("coder", "run"), requested_ttl="bogus",
    )
    assert output == source
    assert meta["state"] == "UNSUPPORTED"


def test_cache_usage_telemetry_normalization():
    usage = extract_cache_usage({"usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 700, "cache_write_tokens": 200},
        "cost": 0.0123,
        "cache_discount": 0.004,
    }})
    assert usage["cached_tokens"] == 700
    assert usage["cache_write_tokens"] == 200
    assert usage["fresh_input_tokens"] == 100
    assert usage["provider_cost"] == Decimal("0.0123")


def test_cache_cost_accounting_cached_tokens_are_not_free():
    actual = cache_aware_actual_usd(
        prompt_tokens=100, completion_tokens=10, cached_tokens=60, cache_write_tokens=20,
        prompt_price_per_token=Decimal("0.000001"),
        completion_price_per_token=Decimal("0.000002"),
        cache_read_price_per_token=Decimal("0.0000001"),
        cache_write_price_per_token=Decimal("0.00000125"),
    )
    assert actual == Decimal("0.000071")
    assert actual > 0


def _gateway(handler, *, stream=False):
    backend_cfg = BackendConfig(
        "openrouter", "http://openrouter", kind="openrouter", cloud=True,
        prompt_cache_enabled=True, prompt_cache_ttl="5m",
    )
    target = ModelTarget(
        "openrouter", "anthropic/claude-opus-5", 10, {"text", "tools"},
        max_output_tokens=100,
        price_usd_per_million_input_tokens="5",
        price_usd_per_million_output_tokens="25",
        price_usd_per_million_cache_read_tokens="0.5",
        price_usd_per_million_cache_write_tokens_5m="6.25",
        price_usd_per_million_cache_write_tokens_1h="10",
    )
    cfg = GatewayConfig(
        backends={"openrouter": backend_cfg},
        aliases={"bossman-opus": AliasConfig("bossman-opus", [target])},
        clients={"core": ClientConfig("core", key="gateway-key", allowed_aliases={"*"})},
    )
    backend = OpenAIBackend(backend_cfg, httpx.MockTransport(handler))
    return create_gateway_app(cfg, ModelRouter(cfg, {"openrouter": backend}))


def _headers(**extra):
    return {
        "Authorization": "Bearer gateway-key",
        "X-Bossman-Cloud-Allowed": "1",
        "X-Bossman-Session-Id": stable_session_id("coder", "run-42"),
        **extra,
    }


def test_gateway_openrouter_payload_and_metrics():
    captured = {}

    async def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "anthropic/claude-opus-5",
            "provider": "Anthropic",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 10, "cost": 0.003,
                      "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 0}},
        })

    with TestClient(_gateway(handler)) as client:
        response = client.post("/v1/chat/completions", headers=_headers(**{"X-Bossman-Cache-TTL": "1h"}),
                               json={"model": "bossman-opus", "max_tokens": 20,
                                     "messages": [{"role": "system", "content": "stable " * 600},
                                                  {"role": "user", "content": "dynamic"}]})
        metrics = client.get("/metrics", headers=_headers()).json()["prompt_cache"]
    assert response.status_code == 200
    assert captured["session_id"] == stable_session_id("coder", "run-42")
    assert captured["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
    assert metrics["state"] == "HOT"
    assert metrics["cached_tokens"] == 600
    assert metrics["actual_cost_usd"] == 0.003
    assert metrics["session_affinity"] is True


def test_cache_failure_fail_open_normal_inference():
    calls = []

    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("messages", [{}])[0].get("content") and isinstance(body["messages"][0]["content"], list):
            return httpx.Response(400, json={"error": "invalid request"})
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "normal"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
        })

    with TestClient(_gateway(handler)) as client:
        response = client.post("/v1/chat/completions", headers=_headers(),
                               json={"model": "bossman-opus", "max_tokens": 10,
                                     "messages": [{"role": "system", "content": "stable"},
                                                  {"role": "user", "content": "task"}]})
        metrics = client.get("/metrics", headers=_headers()).json()["prompt_cache"]
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "normal"
    assert len(calls) == 2
    assert metrics["state"] == "DEGRADED"


def test_streaming_not_broken_and_usage_observed():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: {"choices":[],"usage":{"prompt_tokens":600,"completion_tokens":1,"prompt_tokens_details":{"cached_tokens":550}}}\n\n'
            yield b'data: [DONE]\n\n'

    async def handler(request):
        return httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"})

    with TestClient(_gateway(handler)) as client:
        with client.stream("POST", "/v1/chat/completions", headers=_headers(),
                           json={"model": "bossman-opus", "max_tokens": 10, "stream": True,
                                 "messages": [{"role": "system", "content": "stable " * 600},
                                              {"role": "user", "content": "task"}]}) as response:
            body = b"".join(response.iter_bytes())
        metrics = client.get("/metrics", headers=_headers()).json()["prompt_cache"]
    assert response.status_code == 200
    assert b"[DONE]" in body and b'"content":"ok"' in body
    assert metrics["cached_tokens"] == 550
    assert metrics["state"] == "HOT"
