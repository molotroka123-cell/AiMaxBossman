"""P1 (аудит): failover был неверным на 4xx — ошибка запроса/политики первого
таргета переключала на следующий (потенциально облачный) и гасила здоровье
бэкенда. Теперь на 4xx запроса не переключаемся; на 5xx/429 — переключаемся."""
import json

import httpx
from fastapi.testclient import TestClient

from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget
from bossman.gateway.router import ModelRouter


def _cfg():
    return GatewayConfig(
        backends={
            "bad": BackendConfig(name="bad", base_url="http://bad", max_concurrency=1),
            "good": BackendConfig(name="good", base_url="http://good", max_concurrency=1),
        },
        aliases={"bossman-smart": AliasConfig("bossman-smart", targets=[
            ModelTarget("bad", "bad-model", 10, {"text"}),
            ModelTarget("good", "good-model", 20, {"text"}),
        ])},
        clients={"test": ClientConfig("test", key="secret", requests_per_minute=1000, burst=100, allowed_aliases={"*"})},
        health_ttl_seconds=0,
    )


def _app(bad_status: int):
    c = _cfg()
    good_calls = {"n": 0}

    async def bad(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(bad_status, json={"error": "client error"})

    async def good(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        good_calls["n"] += 1
        body = json.loads(req.content.decode())
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    backends = {
        "bad": OpenAIBackend(c.backends["bad"], httpx.MockTransport(bad)),
        "good": OpenAIBackend(c.backends["good"], httpx.MockTransport(good)),
    }
    return create_gateway_app(c, ModelRouter(c, backends)), backends, good_calls


def _post(app):
    with TestClient(app) as client:
        return client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                           json={"model": "bossman-smart", "messages": []})


def test_4xx_does_not_failover_or_degrade_health():
    app, backends, good_calls = _app(400)
    r = _post(app)
    assert r.status_code == 400            # клиенту вернулась именно 4xx, без эскалации
    assert good_calls["n"] == 0            # второй (потенциально облачный) таргет НЕ вызван
    assert backends["bad"].health.healthy is True   # клиентская ошибка не гасит бэкенд


def test_422_does_not_failover():
    app, backends, good_calls = _app(422)
    r = _post(app)
    assert r.status_code == 422
    assert good_calls["n"] == 0


def test_5xx_still_fails_over():
    app, backends, good_calls = _app(503)
    r = _post(app)
    assert r.status_code == 200            # 5xx → корректный failover
    assert good_calls["n"] == 1
    assert backends["bad"].health.healthy is False


def test_429_overload_fails_over():
    app, backends, good_calls = _app(429)
    r = _post(app)
    assert r.status_code == 200            # 429 (перегрузка) — переключение разрешено
    assert good_calls["n"] == 1
