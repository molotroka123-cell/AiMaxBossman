"""ЭТАП 3: адаптер llm.chat → AI Gateway.

Проверяем, что при заданном BOSSMAN_GATEWAY_URL ядро ходит к моделям через
приватный Gateway ключом BOSSMAN_GATEWAY_CORE_KEY, а не напрямую к LiteLLM
ключом агента; и что Gateway НЕ обходит облачную политику агента (never/ask
проверяются в Core до любой сети).
"""
import json
from pathlib import Path

import httpx
import pytest

import bossman.llm as llm
from bossman.agents import load_agent
from bossman.gateway.client import GatewayClient
from bossman.llm import CloudDenied, chat

AGENTS_DIR = Path(__file__).parent.parent / "agents"


async def _inject_gateway(monkeypatch, handler) -> GatewayClient:
    gc = GatewayClient("http://gw/v1", "core-key")
    # заменяем реальный httpx-клиент на mock-транспорт (без сети)
    await gc._client.aclose()
    gc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm, "_gateway", gc)
    monkeypatch.setattr(llm.settings, "gateway_url", "http://gw/v1")
    monkeypatch.setattr(llm.settings, "gateway_core_key", "core-key")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(llm.db, "execute", _noop)
    return gc


async def test_chat_routes_through_gateway_when_configured(monkeypatch):
    captured: dict = {}

    def handler(req):
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        captured["model"] = json.loads(req.content)["model"]
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })

    gc = await _inject_gateway(monkeypatch, handler)
    try:
        agent = load_agent(AGENTS_DIR / "analyst")
        # локальный алиас — облачная политика не при делах, идём через Gateway
        msg = await chat(agent, [{"role": "user", "content": "привет"}],
                         alias="bossman-fast", run_id=1)
    finally:
        await gc.close()

    assert msg["content"] == "ok"
    assert msg["_usage"]["prompt_tokens"] == 5
    assert captured["url"] == "http://gw/v1/chat/completions"
    # ключ Gateway, НЕ ключ агента и не litellm_master_key
    assert captured["auth"] == "Bearer core-key"
    assert captured["model"] == "bossman-fast"


async def test_gateway_does_not_bypass_cloud_policy(monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "x"}}]})

    gc = await _inject_gateway(monkeypatch, handler)
    try:
        analyst = load_agent(AGENTS_DIR / "analyst")  # cloud_policy=never
        with pytest.raises(CloudDenied):
            await chat(analyst, [{"role": "user", "content": "секрет"}], alias="claude-heavy")
    finally:
        await gc.close()
    # политика отбила запрос ДО Gateway — в сеть (mock) ничего не ушло
    assert calls["n"] == 0
