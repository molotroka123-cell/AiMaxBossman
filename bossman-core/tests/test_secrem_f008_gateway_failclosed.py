"""SECREM F-008 — Gateway: облако fail-closed; эмбеддинги под политикой; аудит
по РАЗРЕШЁННОМУ маршруту; счётчик cloud_requests_total.

REPRO (Fable 5.1): `x-bossman-cloud-allowed` отсутствует → раньше "1" (разрешено);
/v1/embeddings политику не проверял; llm.py считал облачность по префиксу алиаса.
"""
from __future__ import annotations

import httpx
import pytest

from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.client import GatewayClient
from bossman.gateway.config import AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget
from bossman.gateway.router import ModelRouter
from bossman import llm


def _cfg(*, local=True, cloud=True):
    backends, targets = {}, []
    if local:
        backends["ollama"] = BackendConfig("ollama", "http://local", cloud=False)
        targets.append(ModelTarget("ollama", "qwen", 10, {"text", "embeddings"}))
    if cloud:
        backends["openrouter"] = BackendConfig("openrouter", "http://cloud", cloud=True)
        targets.append(ModelTarget("openrouter", "gpt-4o", 100, {"text", "embeddings"}))
    return GatewayConfig(backends=backends, aliases={"smart": AliasConfig("smart", targets)},
                         clients={"core": ClientConfig("core", key=None)},
                         allow_unauthenticated_loopback=True, metrics_enabled=True)


def _client(cfg, hits):
    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if "/embeddings" in str(request.url):
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}], "usage": {}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    t = httpx.MockTransport(handler)
    backends = {n: OpenAIBackend(c, t) for n, c in cfg.backends.items()}
    app = create_gateway_app(cfg, router=ModelRouter(cfg, backends))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


BODY = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.parametrize("headers", [{}, {"x-bossman-cloud-allowed": "maybe"},
                                     {"x-bossman-cloud-allowed": ""}])
async def test_repro_missing_or_unknown_header_is_closed(headers):
    hits = []
    async with _client(_cfg(local=False), hits) as c:
        r = await c.post("/v1/chat/completions", json=BODY, headers=headers)
    assert r.status_code == 403, r.text
    assert hits == []                                   # облако не тронуто


async def test_explicit_one_allows_cloud_and_is_audited():
    hits = []
    async with _client(_cfg(local=False), hits) as c:
        r = await c.post("/v1/chat/completions", json=BODY, headers={"x-bossman-cloud-allowed": "1"})
        assert r.status_code == 200 and r.headers["x-bossman-cloud"] == "1"
        m = (await c.get("/metrics")).json()
    assert m["cloud_requests_total"] == 1 and len(hits) == 1


async def test_local_route_reports_cloud_zero_and_counter_unchanged():
    hits = []
    async with _client(_cfg(cloud=True), hits) as c:
        r = await c.post("/v1/chat/completions", json=BODY)      # без заголовка → только local
        assert r.status_code == 200 and r.headers["x-bossman-cloud"] == "0"
        assert r.headers["x-bossman-backend"] == "ollama"
        m = (await c.get("/metrics")).json()
    assert m["cloud_requests_total"] == 0


async def test_variant_embeddings_are_gated_too():
    hits = []
    body = {"model": "smart", "input": "secret text"}
    async with _client(_cfg(local=False), hits) as c:
        r = await c.post("/v1/embeddings", json=body)
        assert r.status_code == 403 and hits == []
        r = await c.post("/v1/embeddings", json=body, headers={"x-bossman-cloud-allowed": "true"})
        assert r.status_code == 200 and len(hits) == 1


async def test_variant_stream_header_is_upper_bound():
    hits = []
    async with _client(_cfg(local=False), hits) as c:
        r = await c.post("/v1/chat/completions", json={**BODY, "stream": True},
                         headers={"x-bossman-cloud-allowed": "1"})
        assert r.headers.get("x-bossman-cloud") == "1"
        r = await c.post("/v1/chat/completions", json={**BODY, "stream": True})
        assert r.status_code == 403


# ------------------------------------------------------------ клиент/аудит

async def test_client_defaults_closed_and_captures_route_cloudness():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["hdr"] = request.headers.get("x-bossman-cloud-allowed")
        return httpx.Response(200, json={"choices": []}, headers={"x-bossman-cloud": "1"})
    gc = GatewayClient(base_url="http://gw/v1", api_key="k")
    gc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    data = await gc.chat(model="smart", messages=[])
    assert seen["hdr"] == "0" and data["_bossman_cloud"] is True
    await gc.chat(model="smart", messages=[], cloud_allowed=True)
    assert seen["hdr"] == "1"
    seen.clear()
    await gc.embeddings(model="smart", input="x")
    assert seen["hdr"] == "0"


def test_resolved_cloud_prefers_gateway_header_over_alias_prefix():
    assert llm.resolved_cloud("bossman-smart", {"_bossman_cloud": True}) is True
    assert llm.resolved_cloud("bossman-smart", {"_bossman_cloud": False}) is False
    assert llm.resolved_cloud("bossman-smart", {}) is llm.is_cloud("bossman-smart")
    cloud_alias = llm.CLOUD_PREFIXES[0] + "gpt"
    assert llm.is_cloud(cloud_alias)
    assert llm.resolved_cloud(cloud_alias, {"_bossman_cloud": False}) is True  # алиас облачный → облако


def test_agent_cloud_allowed_only_by_policy():
    class A:
        cloud_policy = "never"
    assert llm._agent_cloud_allowed(A()) is False
    A.cloud_policy = "ask"
    assert llm._agent_cloud_allowed(A()) is False
    assert llm._agent_cloud_allowed(A(), "owner") is True
    A.cloud_policy = "allowed"
    assert llm._agent_cloud_allowed(A()) is True
