"""Owner defect 23.10: the chat agent «Gemma 4 uncensored» (models row 98, `gemma4-31b-uncensored:latest`,
loopback Ollama /v1) has context_window 32768, yet `ollama ps` showed it loaded at 8192: every chat was
silently cut to 8k. Ollama's OpenAI-compatible /v1 drops any per-request context size, and the registry
window was never sent anywhere. The window now reaches Ollama as a derived tag with `num_ctx` baked in.

No network: httpx.MockTransport.
"""
import json
from types import SimpleNamespace

import httpx
import pytest

from bcc import providers
from bcc.providers import OpenAICompatAdapter, build_adapter
from bcc.registry import Registry

OLLAMA_V1 = "http://127.0.0.1:11434/v1"
GEMMA = "gemma4-31b-uncensored:latest"
GEMMA_32K = "gemma4-31b-uncensored-ctx32k:latest"


@pytest.fixture(autouse=True)
def _fresh_tag_cache(monkeypatch):
    monkeypatch.setattr(providers, "_OLLAMA_CTX_TAGS", {}, raising=False)


def _ollama(seen: list[tuple[str, str, dict]], *, parameters: str = 'stop "<end_of_turn>"',
            tags: tuple[str, ...] = (GEMMA,), create_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/show":
            return httpx.Response(200, json={"parameters": parameters, "model_info": {}})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": t} for t in tags]})
        if path == "/api/create":
            return httpx.Response(create_status, json={"status": "success"} if create_status < 400
                                  else {"error": "create failed"})
        if path.endswith("/chat/completions"):
            return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {
                "role": "assistant", "content": "ok"}, "finish_reason": "stop"}]})
        return httpx.Response(404, json={"error": f"unexpected {path}"})
    return httpx.MockTransport(handler)


def _chat_bodies(seen):
    return [body for _m, path, body in seen if path.endswith("/chat/completions")]


def _paths(seen):
    return [path for _m, path, _b in seen]


async def test_registry_row_window_reaches_loopback_ollama():
    """The registry's own adapter factory carries the row's context_window to the request Ollama runs."""
    seen: list = []
    fake_registry = SimpleNamespace(vault=SimpleNamespace(decrypt=lambda _enc: None))
    adapter = Registry._default_adapter(
        fake_registry,
        {"id": 98, "name": GEMMA, "context_window": 32768},
        {"name": "local-ollama-11434", "kind": "openai_compat", "base_url": OLLAMA_V1, "api_key_enc": None})
    adapter.transport = _ollama(seen)

    result = await adapter.chat(GEMMA, [{"role": "user", "content": "привет"}], max_tokens=16)

    assert result.text == "ok"
    create = [body for _m, path, body in seen if path == "/api/create"]
    assert create == [{"model": GEMMA_32K, "from": GEMMA, "stream": False, "parameters": {"num_ctx": 32768}}]
    assert _chat_bodies(seen)[-1]["model"] == GEMMA_32K


async def test_derived_tag_is_made_once_per_process():
    seen: list = []
    adapter = OpenAICompatAdapter(base_url=OLLAMA_V1, num_ctx=32768, transport=_ollama(seen))
    await adapter.chat(GEMMA, [{"role": "user", "content": "1"}])
    again = OpenAICompatAdapter(base_url=OLLAMA_V1, num_ctx=32768, transport=_ollama(seen))
    await again.chat(GEMMA, [{"role": "user", "content": "2"}])
    assert _paths(seen).count("/api/create") == 1
    assert _paths(seen).count("/api/show") == 1
    assert [b["model"] for b in _chat_bodies(seen)] == [GEMMA_32K, GEMMA_32K]


async def test_existing_derived_tag_is_reused_without_create():
    seen: list = []
    adapter = OpenAICompatAdapter(base_url=OLLAMA_V1, num_ctx=32768,
                                  transport=_ollama(seen, tags=(GEMMA, GEMMA_32K)))
    await adapter.chat(GEMMA, [{"role": "user", "content": "x"}])
    assert "/api/create" not in _paths(seen)
    assert _chat_bodies(seen)[-1]["model"] == GEMMA_32K


async def test_tag_that_already_has_the_window_is_used_as_is():
    """A Modelfile num_ctx at or above the registry window: no new tag, and never a smaller window."""
    seen: list = []
    adapter = OpenAICompatAdapter(base_url=OLLAMA_V1, num_ctx=32768,
                                  transport=_ollama(seen, parameters="num_ctx                        65536\n"
                                                                     'stop "<end_of_turn>"'))
    await adapter.chat(GEMMA, [{"role": "user", "content": "x"}])
    assert _paths(seen) == ["/api/show", "/v1/chat/completions"]
    assert _chat_bodies(seen)[-1]["model"] == GEMMA


async def test_default_window_row_is_left_to_the_runner():
    seen: list = []
    adapter = OpenAICompatAdapter(base_url=OLLAMA_V1, num_ctx=8192, transport=_ollama(seen))
    await adapter.chat(GEMMA, [{"role": "user", "content": "x"}])
    assert _paths(seen) == ["/v1/chat/completions"]
    assert _chat_bodies(seen)[-1]["model"] == GEMMA


async def test_failed_create_keeps_old_behaviour_and_retries_later():
    seen: list = []
    adapter = OpenAICompatAdapter(base_url=OLLAMA_V1, num_ctx=32768, transport=_ollama(seen, create_status=500))
    result = await adapter.chat(GEMMA, [{"role": "user", "content": "x"}])
    assert result.text == "ok"
    assert _chat_bodies(seen)[-1]["model"] == GEMMA
    await adapter.chat(GEMMA, [{"role": "user", "content": "y"}])
    assert _paths(seen).count("/api/create") == 2                 # a failure is not remembered as "done"


@pytest.mark.parametrize("base", ["https://openrouter.ai/api/v1", "https://integrate.api.nvidia.com/v1",
                                  "http://127.0.0.1:8083/v1", "http://192.168.1.5:11434/v1"])
async def test_cloud_and_other_servers_get_the_request_unchanged(base):
    seen: list = []
    adapter = build_adapter("openai_compat", base, "k", transport=_ollama(seen), num_ctx=32768)
    await adapter.chat("some/model", [{"role": "user", "content": "x"}])
    assert _paths(seen) == [httpx.URL(base).path.rstrip("/") + "/chat/completions"]
    body = _chat_bodies(seen)[-1]
    assert body["model"] == "some/model"
    assert "options" not in body and "num_ctx" not in body
