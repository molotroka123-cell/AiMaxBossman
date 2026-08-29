import json

import httpx
import pytest
from fastapi.testclient import TestClient

from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget
from bossman.gateway.router import ModelRouter


def cfg(key="secret"):
    return GatewayConfig(
        backends={
            "bad": BackendConfig(name="bad", base_url="http://bad", max_concurrency=1),
            "good": BackendConfig(name="good", base_url="http://good", max_concurrency=1),
        },
        aliases={
            "bossman-smart": AliasConfig("bossman-smart", targets=[
                ModelTarget("bad", "bad-model", 10, {"text"}),
                ModelTarget("good", "good-model", 20, {"text"}),
            ]),
            "bossman-vision": AliasConfig("bossman-vision", targets=[ModelTarget("good", "vision-model", 10, {"text","vision"})], required_capabilities={"vision"}),
        },
        clients={"test": ClientConfig("test", key=key, requests_per_minute=1000, burst=100, allowed_aliases={"*"})},
        health_ttl_seconds=0,
    )


def transports():
    async def bad(req):
        return httpx.Response(503, json={"error":"down"})
    async def good(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data":[]})
        body = json.loads(req.content.decode())
        if req.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"id":"x","object":"chat.completion","model":body["model"],"choices":[{"index":0,"message":{"role":"assistant","content":"ok"}}],"usage":{"prompt_tokens":3,"completion_tokens":1}})
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"object":"list","data":[{"embedding":[0.1,0.2],"index":0}],"model":body["model"]})
        return httpx.Response(200, json={"id":"r","model":body["model"],"output":[]})
    return httpx.MockTransport(bad), httpx.MockTransport(good)


def make_app():
    c = cfg()
    bad_t, good_t = transports()
    router = ModelRouter(c, {
        "bad": OpenAIBackend(c.backends["bad"], bad_t),
        "good": OpenAIBackend(c.backends["good"], good_t),
    })
    return create_gateway_app(c, router)


def test_auth_required():
    with TestClient(make_app()) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/v1/models", headers={"Authorization":"Bearer secret"}).status_code == 200


def test_alias_is_exposed_not_backend_model_and_fallback_works():
    with TestClient(make_app()) as client:
        r = client.post("/v1/chat/completions", headers={"Authorization":"Bearer secret"}, json={"model":"bossman-smart","messages":[{"role":"user","content":"hi"}]})
        assert r.status_code == 200
        assert r.json()["model"] == "bossman-smart"
        assert r.headers["x-bossman-backend"] == "good"
        assert r.headers["x-bossman-route-model"] == "good-model"


def test_unknown_alias_rejected():
    with TestClient(make_app()) as client:
        r = client.post("/v1/chat/completions", headers={"Authorization":"Bearer secret"}, json={"model":"raw-secret-model","messages":[]})
        assert r.status_code == 404


def test_vision_requires_vision_capability():
    with TestClient(make_app()) as client:
        payload={"model":"bossman-smart","messages":[{"role":"user","content":[{"type":"text","text":"what?"},{"type":"image_url","image_url":{"url":"data:image/png;base64,x"}}]}]}
        r=client.post("/v1/chat/completions",headers={"Authorization":"Bearer secret"},json=payload)
        assert r.status_code == 404
        payload["model"]="bossman-vision"
        assert client.post("/v1/chat/completions",headers={"Authorization":"Bearer secret"},json=payload).status_code == 200


def test_metrics_account_usage():
    with TestClient(make_app()) as client:
        h={"Authorization":"Bearer secret"}
        client.post("/v1/chat/completions",headers=h,json={"model":"bossman-smart","messages":[]})
        m=client.get("/metrics",headers=h).json()
        assert m["requests_total"] >= 1
        assert m["prompt_tokens"] == 3
        assert m["completion_tokens"] == 1


def test_body_limit():
    c=cfg(); c.request_body_limit_bytes=32
    bad_t,good_t=transports(); router=ModelRouter(c,{"bad":OpenAIBackend(c.backends["bad"],bad_t),"good":OpenAIBackend(c.backends["good"],good_t)})
    with TestClient(create_gateway_app(c,router)) as client:
        r=client.post("/v1/chat/completions",headers={"Authorization":"Bearer secret"},content=b"x"*100)
        assert r.status_code == 413

def test_stream_passthrough():
    c=cfg()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: [DONE]\n\n'
    async def bad(req): return httpx.Response(503,json={"error":"down"})
    async def good(req):
        if req.url.path.endswith('/models'): return httpx.Response(200,json={"data":[]})
        return httpx.Response(200, stream=Stream(), headers={"content-type":"text/event-stream"})
    router=ModelRouter(c,{"bad":OpenAIBackend(c.backends["bad"],httpx.MockTransport(bad)),"good":OpenAIBackend(c.backends["good"],httpx.MockTransport(good))})
    with TestClient(create_gateway_app(c,router)) as client:
        with client.stream("POST","/v1/chat/completions",headers={"Authorization":"Bearer secret"},json={"model":"bossman-smart","messages":[],"stream":True}) as r:
            body=b''.join(r.iter_bytes())
        assert r.status_code==200
        assert b'[DONE]' in body
