"""Адаптеры провайдеров: разбор ответа, usage, заголовки, поведение при недоступности."""
from __future__ import annotations

import json

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
    # system вынесен в отдельное поле (стабильный префикс с cache_control),
    # в messages остаются только user/assistant
    assert seen["payload"]["system"] == [{"type": "text", "text": "ты кратко отвечаешь",
                                          "cache_control": {"type": "ephemeral"}}]
    assert seen["payload"]["messages"] == [{"role": "user", "content": "работаешь?"}]
    assert seen["payload"]["max_tokens"] == 32
    assert result.text == "работаю"
    assert result.usage == {"tokens_in": 21, "tokens_out": 2}   # без измеренного кэша — прежняя форма
    assert result.provider_meta["prompt_cache"] == {"applied": True, "read_tokens": 0,
                                                    "write_tokens": 0, "hit": False}


async def test_anthropic_prompt_cache_prefix_and_measured_usage(monkeypatch):
    """Prompt caching: breakpoint на system и на последнем tool (стабильный
    префикс), messages — после; cache_read/creation из usage — измерение, не оценка."""
    monkeypatch.setenv("BCC_ANTHROPIC_CACHE_TTL", "1h")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["payload"] = json.loads(request.read())
        return httpx.Response(200, json={
            "model": "claude-opus-5", "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 1,
                      "cache_read_input_tokens": 900, "cache_creation_input_tokens": 100},
        })
    adapter = AnthropicAdapter(api_key="k", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
             {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}}]
    res = await adapter.chat("claude-opus-5", [{"role": "system", "content": "S"},
                                               {"role": "user", "content": "q"}], tools=tools)
    p = seen["payload"]
    assert list(p.keys()).index("system") < list(p.keys()).index("messages")   # префикс раньше динамики
    assert p["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in p["tools"][0] and p["tools"][1]["cache_control"]["type"] == "ephemeral"
    assert "cache_control" not in json.dumps(p["messages"])
    assert res.cache_read_tokens == 900 and res.cache_write_tokens == 100
    assert res.tokens_in == 1010                       # input_tokens не включает кэш — суммируем
    assert res.provider_meta["prompt_cache"]["hit"] is True


async def test_anthropic_prompt_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv("BCC_ANTHROPIC_PROMPT_CACHE", "0")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["payload"] = json.loads(request.read())
        return httpx.Response(200, json={"content": [], "usage": {"input_tokens": 1, "output_tokens": 0}})
    adapter = AnthropicAdapter(api_key="k", transport=httpx.MockTransport(handler))
    res = await adapter.chat("m", [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}],
                             tools=[{"type": "function", "function": {"name": "a"}}])
    assert seen["payload"]["system"] == "S" and "cache_control" not in seen["payload"]["tools"][0]
    assert res.provider_meta["prompt_cache"]["applied"] is False


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


# Owner run 2026-09-24: OpenRouter's free Nemotron endpoint answered HTTP 200 with
# {"error": {"code": 503, "message": "Upstream error from Nvidia: Service temporarily
# overloaded"}} about half of the time. Bossman reported that as «пустой ответ (нет
# choices)» — the owner was told the model is silent when the provider was busy.
OVERLOADED = {"id": "gen-1", "error": {"message": "Upstream error from Nvidia: Service temporarily overloaded",
                                       "code": 503, "metadata": {"error_type": "provider_overloaded"}}}
OK_ANSWER = {"choices": [{"message": {"role": "assistant", "content": "да"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 1}}


async def test_in_body_upstream_error_is_named_not_reported_as_empty(monkeypatch):
    monkeypatch.setattr("bcc.providers.TRANSIENT_RETRY_DELAYS", ())
    adapter = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", api_key="k",
                                  transport=httpx.MockTransport(lambda r: httpx.Response(200, json=OVERLOADED)))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m:free", [{"role": "user", "content": "x"}])
    assert "overloaded" in str(info.value) and "503" in str(info.value)
    assert "нет choices" not in str(info.value)
    assert info.value.kind == "rate_limit"          # transient: back off, not "model is silent"


async def test_transient_in_body_error_is_retried_a_bounded_number_of_times(monkeypatch):
    monkeypatch.setattr("bcc.providers.TRANSIENT_RETRY_DELAYS", (0, 0))
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=OVERLOADED if len(calls) < 3 else OK_ANSWER)

    adapter = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(handler))
    result = await adapter.chat("m:free", [{"role": "user", "content": "x"}])
    assert result.text == "да" and len(calls) == 3

    calls.clear()
    adapter = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1",
                                  transport=httpx.MockTransport(lambda r: (calls.append(1),
                                                                           httpx.Response(200, json=OVERLOADED))[1]))
    with pytest.raises(ProviderError):
        await adapter.chat("m:free", [{"role": "user", "content": "x"}])
    assert len(calls) == 3                          # 1 + 2 retries, never unbounded


async def test_non_transient_in_body_error_is_not_retried(monkeypatch):
    monkeypatch.setattr("bcc.providers.TRANSIENT_RETRY_DELAYS", (0, 0))
    calls = []
    body = {"error": {"message": "invalid model", "code": 400}}
    adapter = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1",
                                  transport=httpx.MockTransport(lambda r: (calls.append(1),
                                                                           httpx.Response(200, json=body))[1]))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "x"}])
    assert len(calls) == 1 and "invalid model" in str(info.value) and info.value.kind == "http"


async def test_truly_empty_choices_is_still_empty(monkeypatch):
    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:8080/v1",
                                  transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []})))
    with pytest.raises(ProviderError, match="нет choices"):
        await adapter.chat("m", [{"role": "user", "content": "x"}])
