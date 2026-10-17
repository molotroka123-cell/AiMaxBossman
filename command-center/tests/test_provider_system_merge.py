"""17.10 chat task 87: local Qwen Jinja template rejects a non-leading system message.

The fake endpoint below behaves like the Qwen GGUF chat template served by
Ollama / llama.cpp: any system message that is not messages[0] -> HTTP 500
«System message must be at the beginning». Bossman's engine sends the agent's
system prompt plus recalled memory as two system messages.
"""
from __future__ import annotations

import json

import httpx
import pytest

from bcc import providers
from bcc.providers import OpenAICompatAdapter, ProviderError, merge_system_messages

TEMPLATE_ERROR = ("While executing CallExpression at line 85, column 32 in source: "
                  "raise_exception('System message must be at the beginning.')")

# Shape of an engine run after one tool round: system prompt, recalled memory,
# user task, assistant tool call, tool result, and a late system note.
ENGINE_MESSAGES = [
    {"role": "system", "content": "Ты — оператор L1."},
    {"role": "system", "content": "Память: тесты гонять через pytest -q."},
    {"role": "user", "content": "почини тест"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "print(1)"},
    {"role": "system", "content": "Осталось 3 шага."},
    {"role": "user", "content": "дальше"},
]


def _qwen_template_endpoint(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        seen.append(body)
        msgs = body["messages"]
        if any(m["role"] == "system" for m in msgs[1:]):
            return httpx.Response(500, json={"error": {"message": TEMPLATE_ERROR}})
        return httpx.Response(200, json={
            "model": body["model"],
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    return handler


def _local(seen: list, url: str = "http://127.0.0.1:11434/v1") -> OpenAICompatAdapter:
    return OpenAICompatAdapter(base_url=url, transport=httpx.MockTransport(_qwen_template_endpoint(seen)))


async def test_negative_control_without_merge_the_template_raises(monkeypatch):
    monkeypatch.setattr(providers, "merge_system_messages", lambda m: m)
    seen: list = []
    with pytest.raises(ProviderError) as err:
        await _local(seen).chat("qwen", [dict(m) for m in ENGINE_MESSAGES])
    assert err.value.status_code == 500
    assert "System message must be at the beginning" in str(err.value)


async def test_local_request_has_exactly_one_leading_system_message():
    seen: list = []
    original = [dict(m) for m in ENGINE_MESSAGES]
    result = await _local(seen).chat("qwen", original)
    assert result.text == "ok"
    sent = seen[-1]["messages"]
    assert [m["role"] for m in sent].count("system") == 1
    assert sent[0] == {"role": "system", "content":
                       "Ты — оператор L1.\n\nПамять: тесты гонять через pytest -q.\n\nОсталось 3 шага."}
    # non-system turns: same objects, same order, nothing dropped
    assert sent[1:] == [m for m in ENGINE_MESSAGES if m["role"] != "system"]
    assert original == ENGINE_MESSAGES          # caller's list is not mutated


async def test_streamed_local_request_is_normalized_too():
    seen: list = []
    deltas: list = []

    async def on_delta(text):
        deltas.append(text)

    await _local(seen).chat("qwen", [dict(m) for m in ENGINE_MESSAGES], on_delta=on_delta)
    assert seen and all([m["role"] for m in b["messages"]].count("system") == 1
                        and b["messages"][0]["role"] == "system" for b in seen)


async def test_lan_runner_is_local_and_normalized():
    seen: list = []
    await _local(seen, "http://192.168.1.20:8080/v1").chat("qwen", [dict(m) for m in ENGINE_MESSAGES])
    assert [m["role"] for m in seen[-1]["messages"]].count("system") == 1


async def test_remote_provider_gets_messages_as_sent():
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", api_key="k",
                                  transport=httpx.MockTransport(handler))
    await adapter.chat("m", [dict(m) for m in ENGINE_MESSAGES])
    assert seen[-1]["messages"] == ENGINE_MESSAGES


def test_merge_is_identity_for_single_leading_or_no_system():
    one = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    none = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    assert merge_system_messages(one) is one
    assert merge_system_messages(none) is none


def test_merge_hoists_a_lone_late_system_message():
    msgs = [{"role": "user", "content": "u"}, {"role": "system", "content": "s"}]
    assert merge_system_messages(msgs) == [{"role": "system", "content": "s"},
                                           {"role": "user", "content": "u"}]


def test_merge_keeps_list_content_parts():
    msgs = [{"role": "system", "content": "a"},
            {"role": "system", "content": [{"type": "text", "text": "b"}]},
            {"role": "user", "content": "u"}]
    out = merge_system_messages(msgs)
    assert out[0]["content"] == [{"type": "text", "text": "a"}, {"type": "text", "text": "\n\n"},
                                 {"type": "text", "text": "b"}]
    assert out[1:] == msgs[2:]


def test_merge_skips_empty_system_content_without_losing_others():
    msgs = [{"role": "system", "content": ""}, {"role": "system", "content": "b"},
            {"role": "user", "content": "u"}]
    assert merge_system_messages(msgs)[0]["content"] == "b"
