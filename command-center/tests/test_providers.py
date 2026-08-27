"""Адаптеры провайдеров: разбор ответа, usage, заголовки, поведение при недоступности."""
from __future__ import annotations

import httpx
import pytest

from bcc.providers import AnthropicAdapter, OpenAICompatAdapter, ProviderError, build_adapter


async def test_openai_compat_chat_parses_answer_and_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={
            "model": "local-7b",
            "choices": [{"message": {"role": "assistant", "content": " работаю "},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        })

    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:8080/v1/", api_key="sk-secret1234",
                                 transport=httpx.MockTransport(handler))
    result = await adapter.chat("local-7b", [{"role": "user", "content": "привет"}], max_tokens=64)

    assert seen["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-secret1234"
    assert '"max_tokens": 64' in seen["body"] or '"max_tokens":64' in seen["body"]
    assert result.text == "работаю"
    assert (result.tokens_in, result.tokens_out) == (11, 3)
    assert result.usage == {"tokens_in": 11, "tokens_out": 3}
    assert result.finish == "stop"


async def test_openai_compat_health_offline_when_endpoint_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:9/v1",
                                 transport=httpx.MockTransport(handler))
    health = await adapter.health()
    assert health.status == "offline"
    assert "нет связи" in health.detail


async def test_openai_compat_health_error_on_bad_key():
    adapter = OpenAICompatAdapter(
        base_url="http://127.0.0.1:8080/v1", api_key="bad",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "nope"})))
    health = await adapter.health()
    assert health.status == "error"
    assert "401" in health.detail


async def test_openai_compat_list_models():
    adapter = OpenAICompatAdapter(
        base_url="http://x/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            "data": [{"id": "a"}, {"id": "b"}]})))
    assert await adapter.list_models() == ["a", "b"]


async def test_anthropic_chat_request_and_parsing():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        import json
        seen["payload"] = json.loads(request.read())
        return httpx.Response(200, json={
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "работаю"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 21, "output_tokens": 2},
        })

    adapter = AnthropicAdapter(api_key="sk-ant-key9999",
                               transport=httpx.MockTransport(handler))
    result = await adapter.chat("claude-sonnet-4-5", [
        {"role": "system", "content": "ты кратко отвечаешь"},
        {"role": "user", "content": "работаешь?"},
    ], max_tokens=32)

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-key9999"
    assert seen["headers"]["anthropic-version"] == AnthropicAdapter.version
    # system вынесен в отдельное поле, в messages остаются только user/assistant
    assert seen["payload"]["system"] == "ты кратко отвечаешь"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "работаешь?"}]
    assert seen["payload"]["max_tokens"] == 32
    assert result.text == "работаю"
    assert result.usage == {"tokens_in": 21, "tokens_out": 2}


async def test_anthropic_health_and_missing_key():
    adapter = AnthropicAdapter(api_key=None)
    health = await adapter.health()
    assert health.status == "error" and "api_key" in health.detail

    ok = AnthropicAdapter(api_key="k", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": [{"id": "claude-sonnet-4-5"}]})))
    assert (await ok.health()).status == "ok"
    assert await ok.list_models() == ["claude-sonnet-4-5"]


async def test_build_adapter_unknown_kind():
    with pytest.raises(ProviderError):
        build_adapter("magic")
    assert isinstance(build_adapter("openai_compat", "http://x/v1"), OpenAICompatAdapter)
    assert isinstance(build_adapter("anthropic"), AnthropicAdapter)
