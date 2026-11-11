"""Pult incident 11.11: a LOCAL agent in AUTO/AGENT/CODE/COMPUTER mode answered CLOUD_NOT_ENABLED.

Cause: `send_agent_message` called an agent "cloud" whenever its provider kind was not local/ollama, but
providers.kind is an adapter kind (`openai_compat` / `anthropic`) — local Ollama on 127.0.0.1 is `openai_compat`.
The phone sends allow_cloud=False for local agents, so every local agent turn was refused. A cloud `worker` left in
the session config by an earlier turn had the same effect through the worker branch of the router.

All data here is synthetic; nothing is sent anywhere (FakeAdapter, in-process ASGI client).
"""
from __future__ import annotations

import pytest

from bcc.features.agent_opencode import (
    WORKERS,
    _session_cfg,
    _update_session_cfg,
    agent_model_is_cloud,
    provider_url_is_loopback,
)

from .conftest import FakeAdapter, client_for, make_settings, start_app

LOCAL_URL = "http://127.0.0.1:11434/v1"
CLOUD_URL = "https://openrouter.ai/api/v1"


async def _agent(c, *, provider: str, base_url: str, model: str, alias: str, api_key: str | None = None) -> dict:
    """Provider + model + agent. The model is declared kind=local on purpose: locality must come from the ADDRESS."""
    body = {"name": provider, "kind": "openai_compat", "base_url": base_url}
    if api_key:
        body["api_key"] = api_key
    prov = (await c.post("/api/providers", json=body)).json()
    mdl = (await c.post("/api/models", json={
        "provider_id": prov["id"], "name": model, "alias": alias, "kind": "local", "locality": "local",
        "free": True, "price_in": 0.0, "price_out": 0.0, "pricing_known": True,
        "context_window": 32768, "caps": {"chat": True},
    })).json()
    await c.post(f"/api/models/{mdl['id']}/check")
    agent = (await c.post("/api/agents", json={
        "name": f"Agent {alias}", "role": "assistant", "model_id": mdl["id"],
        "system_prompt": "Синтетический агент теста.",
    })).json()
    assert "id" in agent, agent
    return agent


async def _thread(c, title: str) -> str:
    return (await c.post("/api/chat/threads", json={"title": title})).json()["id"]


def _code(resp) -> str:
    body = resp.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    for src in (body.get("error") if isinstance(body, dict) else None, detail, body):
        if isinstance(src, dict) and src.get("code"):
            return str(src["code"])
    return ""


@pytest.mark.parametrize("mode", ["AUTO", "AGENT"])
async def test_local_agent_on_loopback_openai_compat_is_not_cloud(tmp_path, mode):
    fake = FakeAdapter("Локальный ответ готов.")
    app, svc = await start_app(make_settings(tmp_path), start_workers=False, adapter_factory=lambda m, p: fake)
    try:
        async with client_for(app, svc) as c:
            a_local = await _agent(c, provider="ollama-local", base_url=LOCAL_URL,
                                   model="qwen2.5-coder:7b", alias="qwen-local-7b")
            tid = await _thread(c, "Локальный агент с пульта")
            r = await c.post(f"/api/agent/sessions/{tid}/messages", json={
                "text": "Синтетическая задача для локального агента",
                "mode": mode, "agent_id": a_local["id"], "allow_cloud": False,
            })
            assert r.status_code == 200, r.text
            assert "CLOUD_NOT_ENABLED" not in r.text
            body = r.json()
            assert body["route"]["locality"] == "local"
            assert body["route"]["worker"] is None
            assert body["route"]["model"] == "qwen-local-7b"
            assert body["agent"]["id"] == a_local["id"]
            assert _session_cfg(svc, tid)["allow_cloud"] is False

            # /compact of the same session must not call the local agent "cloud" either
            cmp_res = await c.post(f"/api/agent/sessions/{tid}/compact", json={"instruction": "кратко"})
            assert _code(cmp_res) != "AGENT_COMPACT_CLOUD_BLOCKED", cmp_res.text
    finally:
        await svc.stop()


async def test_stale_cloud_worker_in_session_cfg_is_dropped_for_named_local_agent(tmp_path):
    fake = FakeAdapter("Локальный ответ готов.")
    app, svc = await start_app(make_settings(tmp_path), start_workers=False, adapter_factory=lambda m, p: fake)
    try:
        async with client_for(app, svc) as c:
            a_local = await _agent(c, provider="ollama-local", base_url=LOCAL_URL,
                                   model="qwen2.5-coder:7b", alias="qwen-local-7b")
            tid = await _thread(c, "Старый облачный исполнитель")
            stale = "glm-flash"
            assert stale in WORKERS
            await _update_session_cfg(svc, tid, worker=stale, allow_cloud=False)
            assert _session_cfg(svc, tid)["worker"] == stale

            r = await c.post(f"/api/agent/sessions/{tid}/messages", json={
                "text": "Синтетическая задача после облачного хода",
                "mode": "AUTO", "agent_id": a_local["id"], "allow_cloud": False,
            })
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["route"]["locality"] == "local"
            assert body["route"]["worker"] is None
            cfg = _session_cfg(svc, tid)
            assert cfg["worker"] is None, "stale cloud worker must be cleared from the session config"
            assert cfg["agent_id"] == a_local["id"]

            # Control: without a named agent the stored worker is still honoured (and still gated by allow_cloud).
            tid2 = await _thread(c, "Исполнитель без агента")
            await _update_session_cfg(svc, tid2, worker=stale, allow_cloud=False)
            r2 = await c.post(f"/api/agent/sessions/{tid2}/messages", json={
                "text": "Синтетическая задача без агента", "mode": "AGENT", "allow_cloud": False,
            })
            assert r2.status_code == 409, r2.text
            assert _code(r2) == "CLOUD_NOT_ENABLED"
            assert _session_cfg(svc, tid2)["worker"] == stale
    finally:
        await svc.stop()


async def test_agent_on_public_host_is_still_refused_without_allow_cloud(tmp_path):
    """NEGATIVE control: the model row says kind=local, but the provider address is a public https host."""
    fake = FakeAdapter("Этот ответ не должен понадобиться.")
    app, svc = await start_app(make_settings(tmp_path), start_workers=False, adapter_factory=lambda m, p: fake)
    try:
        async with client_for(app, svc) as c:
            a_cloud = await _agent(c, provider="openrouter-cloud", base_url=CLOUD_URL,
                                   model="z-ai/glm-4.5-flash:free", alias="glm-synthetic",
                                   api_key="sk-or-test-key-123456")
            tid = await _thread(c, "Облако выключено")
            r = await c.post(f"/api/agent/sessions/{tid}/messages", json={
                "text": "Синтетическая задача для облачного агента",
                "mode": "AUTO", "agent_id": a_cloud["id"], "allow_cloud": False,
            })
            assert r.status_code == 409, r.text
            assert _code(r) == "CLOUD_NOT_ENABLED"
    finally:
        await svc.stop()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1", "http://127.0.0.1", "http://127.8.9.10:8090/v1", "http://localhost:11434/v1",
    "http://LOCALHOST:8090", "http://[::1]:11434/v1", "https://localhost/v1", "  http://127.0.0.1:11434/v1  ",
])
def test_loopback_urls_are_local(url):
    assert provider_url_is_loopback(url) is True
    assert agent_model_is_cloud({"kind": "local", "name": "qwen3-coder:30b"},
                                {"kind": "openai_compat", "base_url": url}) is False


@pytest.mark.parametrize("url", [
    "https://openrouter.ai/api/v1", "https://api.anthropic.com", "http://192.168.1.20:11434/v1", "http://10.0.0.5:8000",
    "http://0.0.0.0:11434", "http://169.254.169.254/latest", "http://host.docker.internal:11434",
    "http://127.0.0.1.evil.example/v1", "http://localhost.evil.example/v1", "http://evil.example/?u=127.0.0.1",
    "http://127.0.0.1@evil.example/v1", "127.0.0.1:11434", "localhost", "not a url", "http://", "http://[::1", "",
    None, 12345,
])
def test_non_loopback_or_garbage_urls_are_cloud(url):
    assert provider_url_is_loopback(url) is False
    assert agent_model_is_cloud({"kind": "local", "name": "qwen3-coder:30b"},
                                {"kind": "openai_compat", "base_url": url}) is True


def test_model_kind_and_ollama_cloud_tag_win_over_a_loopback_address():
    loop = {"kind": "openai_compat", "base_url": LOCAL_URL}
    assert agent_model_is_cloud({"kind": "cloud", "name": "anything"}, loop) is True
    assert agent_model_is_cloud({"kind": "cloud", "name": "anything"}, None) is True
    # loopback Ollama serving a `:cloud` model forwards the prompt off the PC
    assert agent_model_is_cloud({"kind": "local", "name": "minimax-m3.1-flash:cloud"}, loop) is True
    # the adapter kind alone decides nothing
    assert agent_model_is_cloud({"kind": "local", "name": "qwen3-coder:30b"}, {"kind": "anthropic", "base_url": LOCAL_URL}) is False
    # no provider row at all: only the model kind is known (unchanged behaviour)
    assert agent_model_is_cloud({"kind": "local", "name": "qwen3-coder:30b"}, None) is False
    assert agent_model_is_cloud(None, None) is False
