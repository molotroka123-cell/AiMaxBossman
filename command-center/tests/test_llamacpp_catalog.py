"""llama-server catalog contracts; no model weights or external network needed.

Upstream: ggml-org/llama.cpp tools/server/README.md, model info and router API.
These tests prove API handling, not inference quality or AMD acceleration.
"""
import json

import httpx
import pytest

from bcc.discovery import discover
from bcc.providers import OpenAICompatAdapter, ProviderError


def adapter_for(body):
    return OpenAICompatAdapter(
        base_url="http://127.0.0.1:8080/v1", api_key="test-local-only",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))


async def test_loading_model_stays_discoverable_but_is_not_reported_ready(tmp_path):
    body = {"data": [{"id": "bossman-local", "owned_by": "llamacpp", "meta": None}]}
    adapter = adapter_for(body)
    assert await adapter.list_models() == ["bossman-local"]
    health = await adapter.health()
    assert health.status == "error" and "загружает" in health.detail
    result = await discover(endpoints=[("llama.cpp", adapter.base_url)],
                            model_dirs=[str(tmp_path)], transport=adapter.transport)
    row = result["endpoints"][0]
    assert not row["ok"] and result["online"] == 0
    assert row["models"] == ["bossman-local"]
    assert row["model_details"][0]["state"] == "loading"


async def test_router_catalog_preserves_facts_but_never_exposes_launch_secrets():
    adapter = adapter_for({"data": [{
        "id": "C:\\Models\\Qwen.gguf", "owned_by": "llamacpp",
        "meta": {"n_ctx_train": 32768, "size": 4000000000, "n_params": 7000000000,
                 "private_field": "secret", "n_embd": 4096},
        "status": {"value": "loaded", "args": ["--api-key", "never-export-this"]},
    }]})
    info = await adapter.list_model_info()
    assert info == [{"id": "C:\\Models\\Qwen.gguf", "owned_by": "llamacpp",
                     "meta": {"n_ctx_train": 32768, "size": 4000000000, "n_params": 7000000000},
                     "state": "loaded"}]
    assert (await adapter.health()).status == "ok"
    assert "never-export-this" not in json.dumps(info)


@pytest.mark.parametrize("state", ["unloaded", "sleeping"])
async def test_router_autoload_catalog_does_not_trigger_generation(state):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": [{"id": "local", "status": {"value": state}}]})
    adapter = OpenAICompatAdapter(base_url="http://localhost:8080/v1",
                                 transport=httpx.MockTransport(handle))
    assert (await adapter.health()).status == "ok"  # catalog available; inference not measured
    assert calls == [("GET", "/v1/models")]


@pytest.mark.parametrize("status", [{"value": "loading"}, {"value": "downloading"},
                                    {"value": "unloaded", "failed": True}])
async def test_router_unavailable_state_without_owned_by(status):
    adapter = adapter_for({"data": [{"id": "local", "status": status}]})
    assert (await adapter.health()).status == "error"


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": {}}, {"data": [None]},
                                  {"data": [{"id": 12}]}])
async def test_malformed_catalog_is_protocol_error_not_false_health(body):
    adapter = adapter_for(body)
    with pytest.raises(ProviderError) as exc:
        await adapter.list_models()
    assert exc.value.kind == "protocol"
    assert (await adapter.health()).status == "error"


async def test_empty_catalog_and_html_page_are_not_healthy():
    assert (await adapter_for({"data": []}).health()).status == "error"
    adapter = OpenAICompatAdapter(base_url="http://localhost:8080/v1",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html>Web UI</html>")))
    assert (await adapter.health()).status == "error"


async def test_llama_chat_uses_existing_authenticated_tool_path():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"model": "bossman-local", "choices": [{
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"notes.txt"}'}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 14, "completion_tokens": 7}})
    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:8080/v1", api_key="local-test",
                                 transport=httpx.MockTransport(handle))
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    result = await adapter.chat("bossman-local", [{"role": "user", "content": "Прочитай заметки"}],
                                tools=tools, tool_choice="auto", max_tokens=64)
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer local-test"
    assert request.url.path == "/v1/chat/completions"
    assert json.loads(request.content)["tools"] == tools
    assert json.loads(request.content)["max_tokens"] == 64
    assert result.tool_calls[0].arguments == {"path": "notes.txt"}
    assert result.usage == {"tokens_in": 14, "tokens_out": 7}
    assert len(seen) == 1  # adapter returns tool request; existing permission layer executes it
