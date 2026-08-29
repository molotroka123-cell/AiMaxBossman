"""Stage 9 — Gateway E2E: Core (llm.chat) → GatewayClient → Gateway ASGI → backends.

Проверяются ШВЫ живой цепочки: облачная политика (never/ask/allowed) на реальном
Gateway, failover 5xx / отказ failover 4xx, circuit breaker fast-fail,
correlation request_id/run_id. Никакой сети: backends через MockTransport.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import bossman.llm as llm
from bossman.agents import load_agent
from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.client import GatewayClient
from bossman.gateway.config import (AliasConfig, BackendConfig, ClientConfig,
                                    GatewayConfig, ModelTarget)
from bossman.gateway.router import ModelRouter
from bossman.llm import CloudDenied, NeedsCloudApproval, chat
from pathlib import Path

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _cfg() -> GatewayConfig:
    return GatewayConfig(
        backends={
            "local": BackendConfig(name="local", base_url="http://local", max_concurrency=2),
            "cloud": BackendConfig(name="cloud", base_url="http://cloud", max_concurrency=2,
                                   cloud=True),
            "dead": BackendConfig(name="dead", base_url="http://dead", timeout_seconds=1,
                                  max_concurrency=1),
        },
        aliases={
            # cloud-only: при запрете облака данных наружу не уходит вовсе
            "cloud-only": AliasConfig("cloud-only", targets=[
                ModelTarget("cloud", "cloud-model", 10, {"text"})]),
            # failover: мёртвый → живой
            "bossman-fast": AliasConfig("bossman-fast", targets=[
                ModelTarget("dead", "dead-model", 10, {"text"}),
                ModelTarget("local", "local-model", 20, {"text"})]),
        },
        clients={"core": ClientConfig("core", key="core-key", requests_per_minute=1000,
                                      burst=100, allowed_aliases={"*"})},
        health_ttl_seconds=0,
    )


def _handler(calls: dict, *, local_status: int = 200, cloud_status: int = 200,
             dead_status: int = 200):
    def handle(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        calls.setdefault(host, []).append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        if dead_status != 200 and host == "dead":
            return httpx.Response(dead_status, json={"error": "bad request"})
        if local_status != 200 and host == "local":
            return httpx.Response(local_status, json={"error": "boom"})
        if cloud_status != 200 and host == "cloud":
            return httpx.Response(cloud_status, json={"error": "boom"})
        body = json.loads(req.content.decode())
        return httpx.Response(200, json={
            "id": "r1", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
    return handle


def _gateway(handler):
    c = _cfg()
    router = ModelRouter(c, {name: OpenAIBackend(bc, httpx.MockTransport(handler))
                             for name, bc in c.backends.items()})
    return create_gateway_app(c, router)


async def _inject(monkeypatch, app, *, request_headers: dict | None = None):
    """llm.chat ходит в НАСТОЯЩИЙ gateway ASGI через ASGITransport."""
    gc = GatewayClient("http://gw/v1", "core-key")
    await gc._client.aclose()
    gc._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    if request_headers:
        orig_chat = gc.chat

        async def chat_with_headers(**kw):
            kw.setdefault("extra_headers", request_headers)
            return await orig_chat(**kw)
    monkeypatch.setattr(llm, "_gateway", gc)
    monkeypatch.setattr(llm.settings, "gateway_url", "http://gw/v1")
    monkeypatch.setattr(llm.settings, "gateway_core_key", "core-key")

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(llm.db, "execute", _noop)
    return gc


async def test_stage9_never_blocks_cloud_zero_calls(monkeypatch):
    """cloud_policy=never + cloud-only алиас: CloudDenied, в cloud-бэкенд НЕ ушло НИЧЕГО."""
    calls: dict = {}
    gc = await _inject(monkeypatch, _gateway(_handler(calls)))
    try:
        agent = load_agent(AGENTS_DIR / "analyst")     # cloud_policy=never
        with pytest.raises(CloudDenied):
            await chat(agent, [{"role": "user", "content": "секрет"}], alias="cloud-only", run_id=1)
    finally:
        await gc.close()
    assert not any(r.url.path.endswith("/chat/completions") for r in calls.get("cloud", []))


async def test_stage9_ask_requires_approval(monkeypatch):
    calls: dict = {}
    gc = await _inject(monkeypatch, _gateway(_handler(calls)))
    try:
        import bossman.agents as agents_mod
        agent = load_agent(AGENTS_DIR / "coder")       # cloud_policy=ask
        with pytest.raises(NeedsCloudApproval):
            await chat(agent, [{"role": "user", "content": "вопрос"}],
                       alias="cloud-only", run_id=1)
    finally:
        await gc.close()
    # без подтверждения в облако не уходит и при ask
    assert not any(r.url.path.endswith("/chat/completions") for r in calls.get("cloud", []))


async def test_stage9_allowed_permits_cloud(monkeypatch):
    calls: dict = {}
    gc = await _inject(monkeypatch, _gateway(_handler(calls)))
    try:
        agent = load_agent(AGENTS_DIR / "coder")
        agent.cloud_policy = "allowed"
        msg = await chat(agent, [{"role": "user", "content": "ok?"}],
                         alias="cloud-only", run_id=1)
        assert msg["content"] == "ok"
    finally:
        await gc.close()
    assert any(r.url.path.endswith("/chat/completions") for r in calls.get("cloud", []))


def test_stage9_gateway_cloud_allowed_header_contract():
    """DEC-004: X-Bossman-Cloud-Allowed=0 вырезает cloud-цели (403, ноль звонков);
    =1 разрешает. Ядро шлёт заголовок, Gateway держит политику."""
    calls: dict = {}
    app = _gateway(_handler(calls))
    body = {"model": "cloud-only", "messages": [{"role": "user", "content": "x"}]}
    with TestClient(app) as client:
        denied = client.post("/v1/chat/completions", json=body, headers={
            "Authorization": "Bearer core-key", "X-Bossman-Cloud-Allowed": "0"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "POLICY_DENIED"
        allowed = client.post("/v1/chat/completions", json=body, headers={
            "Authorization": "Bearer core-key", "X-Bossman-Cloud-Allowed": "1"})
        assert allowed.status_code == 200
    assert not any(r.url.path.endswith("/chat/completions") for r in calls.get("cloud", []) \
                   if r.headers.get("x-bossman-cloud-allowed") == "0")
    assert any(r.url.path.endswith("/chat/completions") for r in calls.get("cloud", []))


def test_stage9_gateway_4xx_no_failover_5xx_failover():
    """4xx бэкенда — НЕ failover (ошибка наружу как есть); 5xx — failover на следующую."""
    calls: dict = {}
    app = _gateway(_handler(calls, dead_status=400))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json={"model": "bossman-fast",
                                                      "messages": [{"role": "user", "content": "x"}]},
                        headers={"Authorization": "Bearer core-key"})
        assert r.status_code == 400            # 4xx доходит до клиента, не escalate
    assert not any(r.url.path.endswith("/chat/completions") for r in calls.get("local", []))

    # 5xx: failover на local → 200
    calls2: dict = {}
    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.host == "dead" and not req.url.path.endswith("/models"):
            return httpx.Response(503, json={"error": "down"})
        return _handler(calls2)(req)
    app2 = _gateway(handle)
    with TestClient(app2) as client:
        r = client.post("/v1/chat/completions", json={"model": "bossman-fast",
                                                      "messages": [{"role": "user", "content": "x"}]},
                        headers={"Authorization": "Bearer core-key"})
        assert r.status_code == 200
    assert any(r.url.path.endswith("/chat/completions") for r in calls2.get("local", []))


def test_stage9_breaker_fast_fails_all_open():
    """Оба target'а за автоматом → немедленный 503 NO_BACKENDS_AVAILABLE."""
    calls: dict = {}

    def handle(req: httpx.Request) -> httpx.Response:
        calls.setdefault(req.url.host, 0)
        if not req.url.path.endswith("/models"):
            calls[req.url.host] += 1
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json={"data": []})

    cfg = GatewayConfig(
        backends={"a": BackendConfig(name="a", base_url="http://a", max_concurrency=1),
                  "b": BackendConfig(name="b", base_url="http://b", max_concurrency=1)},
        aliases={"x": AliasConfig("x", targets=[
            ModelTarget("a", "m1", 10, {"text"}), ModelTarget("b", "m2", 20, {"text"})])},
        clients={"c": ClientConfig("c", key="k", requests_per_minute=1000, burst=100,
                                   allowed_aliases={"*"})},
        health_ttl_seconds=0)
    app = create_gateway_app(cfg, ModelRouter(cfg, {
        n: OpenAIBackend(bc, httpx.MockTransport(handle)) for n, bc in cfg.backends.items()}))
    with TestClient(app) as client:
        for _ in range(5):       # размыкаем оба
            client.post("/v1/chat/completions", json={"model": "x", "messages": []},
                        headers={"Authorization": "Bearer k"})
        n_before = sum(calls.values())
        r = client.post("/v1/chat/completions", json={"model": "x", "messages": []},
                        headers={"Authorization": "Bearer k"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "NO_BACKENDS_AVAILABLE"
        assert sum(calls.values()) == n_before       # в бэкенды больше не звонили


def test_stage9_correlation_request_run_id_logged():
    """X-Request-Id/X-Run-Id проходят через Gateway и попадают в лог-строку."""
    import logging
    records: list[logging.LogRecord] = []

    class Cap(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = Cap()
    lg = logging.getLogger("bossman.gateway")
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        with TestClient(_gateway(_handler({}))) as client:
            client.post("/v1/chat/completions",
                        json={"model": "bossman-fast", "messages": [{"role": "user", "content": "x"}]},
                        headers={"Authorization": "Bearer core-key",
                                 "X-Request-Id": "req-e2e-1", "X-Run-Id": "run-e2e-1"})
    finally:
        lg.removeHandler(h)
    line = " ".join(r.getMessage() for r in records)
    assert "req-e2e-1" in line and "run-e2e-1" in line and "alias=bossman-fast" in line
    assert "секрет" not in line and "messages" not in line   # без содержимого запроса
