"""PASS3 — Gateway route emits the shared cache observation from provider usage
(OpenRouter/Anthropic-style cached_tokens), exposed under /metrics.cache_observations."""
from __future__ import annotations

import httpx
import pytest

from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget
from bossman.gateway.router import ModelRouter

BODY = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}


def _cfg():
    return GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://cloud", cloud=True, kind="openrouter")},
        aliases={"smart": AliasConfig("smart", [ModelTarget("openrouter", "anthropic/claude-sonnet-4", 100, {"text"})])},
        clients={"core": ClientConfig("core", key=None)}, allow_unauthenticated_loopback=True,
        metrics_enabled=True)


def _client(usages):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        u = usages[min(calls["n"], len(usages) - 1)]
        calls["n"] += 1
        body = {"choices": [{"message": {"content": "hi"}}]}
        if u is not None:
            body["usage"] = u
        return httpx.Response(200, json=body)
    cfg = _cfg()
    backends = {n: OpenAIBackend(c, httpx.MockTransport(handler)) for n, c in cfg.backends.items()}
    app = create_gateway_app(cfg, router=ModelRouter(cfg, backends))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw",
                             headers={"x-bossman-cloud-allowed": "1"})


async def test_write_then_hit_then_miss_then_unknown_are_classified_from_usage():
    usages = [
        {"prompt_tokens": 1000, "completion_tokens": 5, "prompt_tokens_details": {"cache_write_tokens": 900}},
        {"prompt_tokens": 1000, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 900}},
        {"prompt_tokens": 1000, "completion_tokens": 5},
        None,
    ]
    async with _client(usages) as c:
        for _ in usages:
            r = await c.post("/v1/chat/completions", json=BODY)
            assert r.status_code == 200
        m = (await c.get("/metrics")).json()
    obs = m["cache_observations"]
    assert obs["counts"]["WRITE"] == 1 and obs["counts"]["HIT"] == 1 and obs["counts"]["MISS"] == 1
    assert obs["counts"]["UNKNOWN"] == 1
    assert obs["tokens"] == {"fresh_input": 1200, "cache_read": 900, "cache_write": 900, "output": 15}
    assert obs["eligible_requests"] == 3 and obs["hit_rate_percent"] == pytest.approx(33.3)


async def test_observation_summary_has_no_content_fields():
    async with _client([{"prompt_tokens": 10, "completion_tokens": 1}]) as c:
        await c.post("/v1/chat/completions", json=BODY)
        m = (await c.get("/metrics")).json()
    text = str(m["cache_observations"])
    assert "hi" not in text.split() and "messages" not in text and "content" not in text
