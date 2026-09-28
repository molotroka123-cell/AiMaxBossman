"""RC19 owner defects on the real data (2026-09-28).

1. «Bossman CMD» showed the private thinking of local Ollama Qwen models. Ollama's
   OpenAI-compatible /v1 returns it in `message.reasoning` (content empty until
   the thinking ends); `think: false` is ignored on /v1, `reasoning_effort: "none"`
   switches it off (measured on the owner host, Ollama 0.34.4). The CMD view
   also no longer prints reasoning text unless asked (/expand, --verbose).
2. Provider «local-main» (127.0.0.1:8081) now answers as a different app. A
   local endpoint that is not an OpenAI-compatible model server is «offline»
   (re-probed like any dead endpoint), and an agent whose local model is
   offline is routed to its fallback before the call.

No network: providers use httpx.MockTransport, the backend fakes adapters.
"""
import io
import json
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import agents as agents_t, models as models_t, utcnow
from bcc.features import healing, router as router_feature
from bcc.providers import OpenAICompatAdapter

from .conftest import FakeAdapter
from .helpers import make_stack


def _chat_transport(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "4"}, "finish_reason": "stop"}]})
    return httpx.MockTransport(handler)


async def test_ollama_v1_chat_switches_thinking_off():
    seen: list[dict] = []
    ollama = OpenAICompatAdapter(base_url="http://127.0.0.1:11434/v1", transport=_chat_transport(seen))
    result = await ollama.chat("bossman-fast-qwen36-35b-a3b-q5:latest",
                               [{"role": "user", "content": "2+2?"}], max_tokens=32)
    assert result.text == "4"
    assert seen[-1]["reasoning_effort"] == "none"


async def test_explicit_reasoning_mode_and_other_servers_are_untouched():
    seen: list[dict] = []
    ollama = OpenAICompatAdapter(base_url="http://localhost:11434/v1", transport=_chat_transport(seen))
    await ollama.chat("m", [{"role": "user", "content": "x"}], reasoning_effort="high")
    assert seen[-1]["reasoning_effort"] == "high"
    for base in ("http://127.0.0.1:8083/v1", "https://openrouter.ai/api/v1"):
        other = OpenAICompatAdapter(base_url=base, api_key="k", transport=_chat_transport(seen))
        await other.chat("m", [{"role": "user", "content": "x"}])
        assert "reasoning_effort" not in seen[-1]


@pytest.mark.parametrize("response", [
    httpx.Response(404, text="Not Found"),
    httpx.Response(200, text="<html>codex app-server</html>"),
    httpx.Response(200, json={"jsonrpc": "2.0", "result": {}}),
])
async def test_wrong_service_on_a_local_port_is_offline(response):
    wrong = OpenAICompatAdapter(base_url="http://127.0.0.1:8081/v1",
                                transport=httpx.MockTransport(lambda request: response))
    health = await wrong.health()
    assert health.status == "offline"
    assert "не сервер моделей" in health.detail


async def test_cloud_refusal_stays_an_error_not_offline():
    cloud = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", api_key="k",
                                transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope")))
    assert (await cloud.health()).status == "error"
    local_key = OpenAICompatAdapter(base_url="http://127.0.0.1:8081/v1", api_key="k",
                                    transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert (await local_key.health()).status == "error"      # owner's key/config, not a dead port


async def test_agent_on_offline_local_model_falls_back_before_the_call(env, monkeypatch):
    seen: list[str] = []

    def factory(model, provider):
        async def record(calls, messages):
            seen.append(model["alias"])
        return FakeAdapter(f"via {model['alias']}", on_chat=record)

    env.svc.registry.adapter_factory = factory
    monkeypatch.setattr(router_feature, "_measure_pool_sync", lambda: (None, "none"))
    monkeypatch.setattr(router_feature, "_ollama_inventory_sync", lambda root: {})
    router_feature._ollama_cache.clear()
    stack = await make_stack(env.client, prompt="привет")        # local-7b on :8080
    while (rid := await env.svc.engine.claim()) is not None:
        await env.svc.engine.execute(rid)
    seen.clear()
    main = (await env.client.post("/api/providers", json={
        "name": "local-main", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8081/v1", "api_key": ""})).json()
    dead = (await env.client.post("/api/models", json={
        "provider_id": main["id"], "name": "qwen38", "alias": "main", "kind": "local"})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == dead["id"]).values(
            status="offline", status_detail="на адресе отвечает не сервер моделей",
            last_check=utcnow()))
        await s.execute(sa.update(models_t).where(models_t.c.id == stack["model"]["id"])
                        .values(status="online"))
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            model_id=dead["id"], fallback_model_id=stack["model"]["id"]))
        await s.commit()
    task = (await env.client.post("/api/tasks", json={
        "title": "t", "prompt": "привет", "agent_id": stack["agent"]["id"],
        "run_now": True})).json()["task"]
    while (rid := await env.svc.engine.claim()) is not None:
        await env.svc.engine.execute(rid)
    final = (await env.client.get(f"/api/tasks/{task['id']}")).json()
    assert seen == ["local-7b"]                   # the dead endpoint was not called first
    assert final["task"]["status"] == "completed"
    assert final["runs"][-1]["route"]["fallback_from"] == "main"


async def test_probe_keeps_rechecking_a_port_taken_by_another_app(env):
    # «error» used to end re-probing; a wrong service is «offline», so it is re-checked
    wrong = httpx.MockTransport(lambda r: httpx.Response(404, text="Not Found"))
    env.svc.registry.adapter_factory = lambda m, p: OpenAICompatAdapter(
        base_url=p["base_url"], transport=wrong)
    main = (await env.client.post("/api/providers", json={
        "name": "local-main", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8081/v1", "api_key": ""})).json()
    m = (await env.client.post("/api/models", json={
        "provider_id": main["id"], "name": "qwen38", "alias": "main", "kind": "local"})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == m["id"]).values(
            status="online", last_check=utcnow() - timedelta(minutes=5)))
        await s.commit()
    assert await healing.probe_local_models(env.svc, {}) == [m["id"]]
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(models_t.c.status, models_t.c.last_check)
                               .where(models_t.c.id == m["id"]))).first()._mapping
    assert row["status"] == "offline"


# ---------------------------------------------------------------- CMD view

def _view(verbose=False):
    pytest.importorskip("rich")
    from bcc.terminal_cli.console import make_console
    from bcc.terminal_cli.human import View
    buf = io.StringIO()
    return View(make_console(stream=buf, plain=True, width=100), plain=True, verbose=verbose), buf


def test_cmd_does_not_print_model_reasoning_by_default():
    view, buf = _view()
    view.on_record({"type": "thinking", "delta": "Here's a thinking process:\n1. Analyze", "step": 1})
    view.on_record({"type": "assistant_message", "text": "4"})
    out = buf.getvalue()
    assert "thinking process" not in out and "Analyze" not in out
    assert "/expand 1" in out and "4" in out
    view.expand(1)                                 # the owner's explicit toggle still works
    assert "thinking process" in buf.getvalue()


def test_cmd_verbose_still_shows_reasoning_preview():
    view, buf = _view(verbose=True)
    view.on_record({"type": "thinking", "delta": "Here's a thinking process:\n1. Analyze", "step": 1})
    assert "thinking process" in buf.getvalue()
