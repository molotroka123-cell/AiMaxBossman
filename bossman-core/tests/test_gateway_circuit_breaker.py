"""Аудит Gateway (reliability): circuit breaker, классификация health-probe,
корреляция запросов.

1) Прерывателя не было: мёртвый бэкенд ретраился каждым запросом на полном
   таймауте. Теперь N неудач подряд (транспорт/таймаут/5xx) размыкают автомат
   на cooldown; resolve() ПРОПУСКАЕТ разомкнутые цели; если разомкнуты все —
   503 сразу. Обычные клиентские 4xx автомат не открывают.
2) Health-probe считал здоровым любой ответ < 500 — 401/403 и 429 рапортовали
   «зелёный» при 100% неработающих запросах. Теперь здоров только 2xx, а
   проба живёт на коротком собственном таймауте, не на таймауте инференса.
3) Gateway был чёрным ящиком: добавлены request_id (эхо-заголовок) и run_id,
   одна строка лога на запрос. Промпты/тела/ключи в лог не попадают.
"""
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import CircuitBreaker, OpenAIBackend
from bossman.gateway.config import (AliasConfig, BackendConfig, ClientConfig,
                                    GatewayConfig, ModelTarget)
from bossman.gateway.router import ModelRouter

PROMPT = "СЕКРЕТНЫЙ_ПРОМПТ_В_ЛОГ_ПОПАДАТЬ_НЕ_ДОЛЖЕН"


def _config(**kw):
    defaults = dict(
        backends={
            "dead": BackendConfig(name="dead", base_url="http://dead", max_concurrency=1),
            "good": BackendConfig(name="good", base_url="http://good", max_concurrency=1),
        },
        aliases={"bossman-smart": AliasConfig("bossman-smart", targets=[
            ModelTarget("dead", "dead-model", 10, {"text"}),
            ModelTarget("good", "good-model", 20, {"text"}),
        ])},
        clients={"test": ClientConfig("test", key="secret", requests_per_minute=100000,
                                      burst=100000, allowed_aliases={"*"})},
        health_ttl_seconds=0,
    )
    defaults.update(kw)
    return GatewayConfig(**defaults)


def _make_app(dead_status: int | None = None, dead_cooldown: float = 30.0,
              alias_targets: list[ModelTarget] | None = None):
    c = _config()
    c.backends["dead"].circuit_cooldown_seconds = dead_cooldown
    if alias_targets is not None:
        c.aliases["bossman-smart"].targets = alias_targets
    calls = {"dead": 0, "good": 0}

    async def dead(req):
        calls["dead"] += 1
        if dead_status is not None:
            return httpx.Response(dead_status, json={"error": "down"})
        raise httpx.ConnectError("connection refused", request=req)

    async def good(req):
        calls["good"] += 1
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(req.content.decode())
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    router = ModelRouter(c, {
        "dead": OpenAIBackend(c.backends["dead"], httpx.MockTransport(dead)),
        "good": OpenAIBackend(c.backends["good"], httpx.MockTransport(good)),
    })
    return create_gateway_app(c, router), calls, c, router.backends


# ---------------------------------------------------------------- circuit breaker


def test_breaker_opens_after_repeated_dead_backend_and_falls_back():
    """3 неудачи подряд открывают автомат; далее dead ПРОПУСКАЕТСЯ целиком —
    запрос уходит на good без попыток мёртвого бэкенда."""
    app, calls, c, backends = _make_app()
    dead_br = backends["dead"].breaker
    for _ in range(3):  # порог по умолчанию — 3
        dead_br.record_failure("HTTP 503")
    assert dead_br.state == "open"
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                        json={"model": "bossman-smart", "messages": []})
        assert r.status_code == 200
        assert calls["dead"] == 0           # разомкнутый автомат: dead не дёргается
        assert calls["good"] == 1
        assert r.headers["x-bossman-backend"] == "good"


def test_circuit_does_not_open_on_client_4xx():
    """4xx — ошибка запроса, а не бэкенда: автомат закрыт, dead вызывается
    каждым запросом, на good не переключаемся."""
    app, calls, c, backends = _make_app(dead_status=400)
    with TestClient(app) as client:
        for _ in range(5):
            r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                            json={"model": "bossman-smart", "messages": []})
            assert r.status_code == 400
    assert calls["dead"] == 5            # пробуют каждый раз: автомат НЕ открывался
    assert calls["good"] == 0
    assert backends["dead"].breaker.state == "closed"


def test_all_backends_open_fast_fails_503():
    """Если разомкнуты ВСЕ цели алиаса — 503 сразу, без вызовов бэкендов."""
    app, calls, c, backends = _make_app(
        alias_targets=[ModelTarget("dead", "dead-model", 10, {"text"})])
    with TestClient(app) as client:
        for _ in range(3):
            r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                            json={"model": "bossman-smart", "messages": []})
            assert r.status_code == 502  # пока автомат закрыт — честный 502 после попытки
        assert calls["dead"] == 3
        r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                        json={"model": "bossman-smart", "messages": []})
        assert r.status_code == 503      # быстрый отказ, бэкенд не дёргается
        assert r.json()["error"]["code"] == "NO_BACKENDS_AVAILABLE"
        assert calls["dead"] == 3


def test_half_open_one_retry_close_on_success_reopen_on_failure():
    """HALF_OPEN: по истечении cooldown разрешена ровно одна пробная попытка;
    успех закрывает автомат, провал переоткрывает."""
    br = CircuitBreaker(failure_threshold=2, cooldown_seconds=30.0, request_timeout_seconds=120.0)
    br.record_failure("HTTP 503")
    br.record_failure("HTTP 503")
    assert br.state == "open"
    assert br.allow_attempt() is False
    br.opened_at = time.monotonic() - 31.0          # cooldown истёк
    assert br.state == "half_open"
    assert br.allow_attempt() is True               # одна пробная попытка
    assert br.allow_attempt() is False              # второй параллельной не даём
    br.record_failure("probe failed")               # провал пробы — переоткрытие
    assert br.state == "open"
    br.opened_at = time.monotonic() - 31.0
    assert br.allow_attempt() is True
    br.record_success()                             # успех — закрыт
    assert br.state == "closed"
    assert br.allow_attempt() is True


def test_half_open_app_level_failure_reopens_breaker():
    """Сквозной сценарий: автомат разомкнут тремя провалами dead → cooldown
    истёк → resolve даёт одну пробную попытку → провал переоткрывает автомат →
    следующие запросы dead снова не трогают."""
    app, calls, c, backends = _make_app(dead_cooldown=0.1)
    for _ in range(3):
        backends["dead"].breaker.record_failure("HTTP 503")
    with TestClient(app) as client:
        time.sleep(0.2)                              # cooldown истёк → half_open
        r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                        json={"model": "bossman-smart", "messages": []})
        assert r.status_code == 200
        assert calls["dead"] == 1                    # пробная попытка ушла на dead
        assert backends["dead"].breaker.state == "open"  # провал переоткрыл
        r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                        json={"model": "bossman-smart", "messages": []})
        assert r.status_code == 200
        assert calls["dead"] == 1                    # снова пропускается


def test_router_skips_open_backend_keeps_policy_denial_priority():
    """resolve() пропускает разомкнутые цели; при политике never и разомкнутом
    локальном бэкенде облако всё равно вырезается — граница не пробивается."""
    from bossman.gateway.router import CloudPolicyDenied

    cfg = GatewayConfig(
        backends={"ollama": BackendConfig("ollama", "http://local", cloud=False),
                  "openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"bossman-smart": AliasConfig("bossman-smart", [
            ModelTarget("ollama", "qwen", 10, {"text"}),
            ModelTarget("openrouter", "gpt-4o", 100, {"text"})])},
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    router = ModelRouter(cfg, {n: OpenAIBackend(b, transport) for n, b in cfg.backends.items()})
    # открываем автомат на локальном бэкенде
    for _ in range(cfg.backends["ollama"].circuit_failure_threshold):
        router.backends["ollama"].breaker.record_failure("HTTP 503")
    # облако разрешено: остаётся только облачная цель
    routes = router.resolve("bossman-smart", cloud_allowed=True)
    assert [r.backend_name for r in routes] == ["openrouter"]
    # never + разомкнутый локальный: безопасный CloudPolicyDenied, данные не уходят
    with pytest.raises(CloudPolicyDenied):
        router.resolve("bossman-smart", cloud_allowed=False)
    assert router.backends["openrouter"].breaker.state == "closed"


# ---------------------------------------------------------------- health probe


async def _probe(status: int | None = None, exc: Exception | None = None):
    async def handler(req):
        if exc is not None:
            raise exc
        return httpx.Response(status, json={})
    b = OpenAIBackend(BackendConfig("t", "http://t"), httpx.MockTransport(handler))
    try:
        return await b.probe()
    finally:
        await b.close()


async def test_probe_2xx_is_healthy_and_sets_checked_at():
    h = await _probe(200)
    assert h.healthy is True
    assert h.error is None
    assert h.checked_at > 0
    assert h.latency_ms is not None


async def test_probe_401_403_credentials_config_failure():
    for status in (401, 403):
        h = await _probe(status)
        assert h.healthy is False
        assert h.checked_at > 0
        assert "credential" in h.error.lower() and "config" in h.error.lower()


async def test_probe_429_throttled_unavailable():
    h = await _probe(429)
    assert h.healthy is False
    assert "throttl" in h.error.lower() or "unavailable" in h.error.lower()


async def test_probe_5xx_404_and_transport_unhealthy():
    h = await _probe(503)
    assert h.healthy is False
    h = await _probe(404)
    assert h.healthy is False and "404" in h.error
    h = await _probe(exc=httpx.ConnectError("boom"))
    assert h.healthy is False and "ConnectError" in h.error


async def test_probe_uses_short_timeout_not_inference_timeout():
    seen = {}

    async def handler(req):
        seen["timeout"] = req.extensions.get("timeout")
        return httpx.Response(200, json={})

    cfg = BackendConfig("t", "http://t", timeout_seconds=120.0, health_timeout_seconds=1.5)
    b = OpenAIBackend(cfg, httpx.MockTransport(handler))
    try:
        await b.probe()
    finally:
        await b.close()
    t = seen["timeout"]
    assert t is not None, "probe должен передавать собственный таймаут"
    assert t["read"] == pytest.approx(1.5)
    assert t["connect"] == pytest.approx(1.5)


# ---------------------------------------------------------------- корреляция


def test_request_id_echoed_and_logged_with_run_id(caplog):
    app, calls, c, backends = _make_app()
    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="bossman.gateway"):
        r = client.post("/v1/chat/completions",
                        headers={"Authorization": "Bearer secret",
                                 "X-Request-Id": "req-abc-123", "X-Run-Id": "run-42"},
                        json={"model": "bossman-smart",
                              "messages": [{"role": "user", "content": PROMPT}]})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "req-abc-123"
    text = caplog.text
    assert "request_id=req-abc-123" in text
    assert "run_id=run-42" in text
    assert "alias=bossman-smart" in text
    assert "backend=good" in text and "model=good-model" in text
    assert "outcome=ok" in text
    assert "fallbacks=1" in text                      # один неудачный dead → fallback
    # приватное не логируется: ни промпт, ни ключ, ни тело запроса
    assert PROMPT not in text
    assert "secret" not in text
    assert "messages" not in text


def test_request_id_generated_when_missing_and_run_id_dash(caplog):
    app, calls, c, backends = _make_app()
    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="bossman.gateway"):
        r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer secret"},
                        json={"model": "bossman-smart", "messages": []})
    assert r.status_code == 200
    rid = r.headers.get("x-request-id")
    assert rid, "ответ обязан нести сгенерированный request id"
    text = caplog.text
    assert f"request_id={rid}" in text
    assert "run_id=-" in text


def test_error_outcome_logged_with_request_id(caplog):
    """Ошибка (4xx без failover) тоже логируется с request_id."""
    app, calls, c, backends = _make_app(dead_status=400,
                              alias_targets=[ModelTarget("dead", "dead-model", 10, {"text"})])
    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="bossman.gateway"):
        r = client.post("/v1/chat/completions",
                        headers={"Authorization": "Bearer secret", "X-Request-Id": "req-err-1"},
                        json={"model": "bossman-smart", "messages": []})
    assert r.status_code == 400
    assert "request_id=req-err-1" in caplog.text
    assert "outcome=error" in caplog.text
    assert "backend=dead" in caplog.text
