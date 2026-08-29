"""Stage 9 — полный агентный smoke: Task → Context compile → Gateway model call →
tool execution → result → journal. Проверяются ШВЫ, не качество LLM.

Модель — через НАСТОЯЩИЙ Gateway ASGI с mock-бэкендом (fake local model).
Live-local режим: BOSSMAN_LIVE_LOCAL_URL задан → тест идёт в реальную модель
(опционально, владельцем), иначе SKIP той части.
"""
import json
import os
from pathlib import Path

import httpx
import pytest

import bossman.llm as llm
from bossman.agents import load_agent
from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import (AliasConfig, BackendConfig, ClientConfig,
                                    GatewayConfig, ModelTarget)
from bossman.gateway.router import ModelRouter
from bossman.toolkit import REGISTRY, ToolContext
from bossman.toolkit.files import fs_read, fs_search
from bossman.toolkit.journal import log as journal_log, search_journal

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _gateway_app(calls: dict):
    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(req.content.decode())
        # «модель» возвращает tool_call на fs.read: проверяем шов model→tool
        return httpx.Response(200, json={
            "id": "g1", "model": body["model"],
            "choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": "PROBE-OK"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 2}})
    cfg = GatewayConfig(
        backends={"local": BackendConfig(name="local", base_url="http://local",
                                         max_concurrency=2)},
        aliases={"bossman-fast": AliasConfig("bossman-fast", targets=[
            ModelTarget("local", "fake-local-model", 10, {"text"})])},
        clients={"core": ClientConfig("core", key="core-key", requests_per_minute=1000,
                                      burst=100, allowed_aliases={"*"})},
        health_ttl_seconds=0)
    router = ModelRouter(cfg, {"local": OpenAIBackend(cfg.backends["local"],
                                                      httpx.MockTransport(handle))})
    return create_gateway_app(cfg, router)


async def test_stage9_full_agent_seams(tmp_path, monkeypatch):
    # 1) Task: агент из yaml + файл задачи в workdir
    agent = load_agent(AGENTS_DIR / "analyst")            # cloud_policy=never
    workdir = tmp_path / "work"; workdir.mkdir()
    (workdir / "task.txt").write_text("stage9 seam probe", encoding="utf-8")
    ctx = ToolContext(agent=agent.name, workdir=workdir)

    # 2) Context compile: системный промпт + состояние задачи в бюджете окна
    from bossman.context_engine.compiler import ContextCompiler
    class _NoRetriever:
        def search(self, *a, **k):
            return []
    class _NoMemory:
        def retrieve(self, *a, **k):
            return []
    compiler = ContextCompiler(_NoRetriever(), _NoMemory())
    compiled = compiler.compile(model="bossman-fast", query="прочитай task.txt",
                                system=agent.title, task_state="step 1/1",
                                model_window=32768)
    assert compiled.sections and compiled.used_tokens <= compiled.budget_tokens
    compiled_query = "\n".join(s.text for s in compiled.sections)

    # 3) Gateway model call: llm.chat через реальный gateway ASGI (mock-бэкенд)
    from bossman.gateway.client import GatewayClient
    calls: list = []
    gc = GatewayClient("http://gw/v1", "core-key")
    await gc._client.aclose()
    gc._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_gateway_app(calls)))
    monkeypatch.setattr(llm, "_gateway", gc)
    monkeypatch.setattr(llm.settings, "gateway_url", "http://gw/v1")
    monkeypatch.setattr(llm.settings, "gateway_core_key", "core-key")

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(llm.db, "execute", _noop)

    msg = await llm.chat(agent, [{"role": "user",
                                  "content": compiled_query + "\nпрочитай task.txt"}],
                         alias="bossman-fast", run_id=99)
    assert msg["content"] == "PROBE-OK"
    # в бэкенд ушёл алиас; cloud (never) не обязан блокировать локальный путь
    assert calls and calls[0].url.host == "local"

    # 4) Tool execution: fs.read по реальному файлу
    res = await fs_read({"path": "task.txt"}, ctx)
    assert "stage9 seam probe" in res.content

    # 5) Journal: запись и поиск по нему
    await journal_log({"text": "stage9: seam smoke пройден"}, ctx)
    found = await search_journal({"query": "seam smoke"}, ctx)
    assert "seam smoke" in found.content

    # 6) Результат: результат модели + данные инструмента согласованы в ответе задачи
    assert "PROBE-OK" in msg["content"] and "stage9 seam probe" in res.content


@pytest.mark.skipif(not os.environ.get("BOSSMAN_LIVE_LOCAL_URL"),
                    reason="BOSSMAN_LIVE_LOCAL_URL не задан — live-local часть пропущена")
async def test_stage9_live_local_optional(monkeypatch):
    """Опциональный live-режим: одна модель, один запрос, без облака."""
    url = os.environ["BOSSMAN_LIVE_LOCAL_URL"].rstrip("/")
    adapter_llm = httpx.AsyncClient(timeout=30)
    try:
        r = await adapter_llm.post(f"{url}/v1/chat/completions", json={
            "model": os.environ.get("BOSSMAN_LIVE_LOCAL_MODEL", "qwen2.5:7b"),
            "messages": [{"role": "user", "content": "Ответь ровно: OK"}],
            "max_tokens": 8, "temperature": 0})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"]
    finally:
        await adapter_llm.aclose()
