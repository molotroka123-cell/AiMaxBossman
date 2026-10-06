"""Jeff 1.8: local Ollama chat never thinks aloud, never returns nothing, never leaks reasoning.

Fakes only (httpx.MockTransport / in-memory adapters): no Ollama, no network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest

from bcc.pit import runtime as rt
from bcc.pit.ollama_native import (EmptyAnswer, LocalOpenAICompatChat, OllamaNativeChatAdapter,
                                   is_native_ollama_url)
from bcc.pit.router import ModelEndpoint
from bcc.providers import ChatResult

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm

NATIVE = "http://127.0.0.1:11434/v1"
COMPAT = "http://127.0.0.1:8080/v1"


def _native(handler):
    return OllamaNativeChatAdapter(NATIVE, transport=httpx.MockTransport(handler))


def _ok(content, **extra):
    body = {"model": "m", "message": {"content": content}, "done": True, "done_reason": "stop",
            "eval_count": 12, "prompt_eval_count": 3}
    body["message"].update(extra)
    return httpx.Response(200, json=body)


def test_native_request_always_disables_thinking():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return _ok("Привет.")

    result = asyncio.run(_native(handler).chat("m", [{"role": "user", "content": "hi"}],
                                               think=True, reasoning_effort="high"))
    assert result.text == "Привет."
    assert seen[0]["think"] is False, "a caller can never turn thinking back on"
    assert "reasoning_effort" not in seen[0] or seen[0]["reasoning_effort"] == "none"


def test_native_never_returns_the_thinking_field_or_inline_reasoning():
    def handler(request):
        return _ok("<think>секретный ход мысли</think>Готовый ответ.",
                   thinking="ещё один ход мысли")

    result = asyncio.run(_native(handler).chat("m", [{"role": "user", "content": "hi"}]))
    assert result.text == "Готовый ответ."
    assert "мысли" not in result.text


def test_native_reasoning_only_answer_is_an_empty_answer_not_a_reply():
    def handler(request):
        return _ok("<think>только рассуждение</think>", thinking="ещё")

    with pytest.raises(EmptyAnswer):
        asyncio.run(_native(handler).chat("m", [{"role": "user", "content": "hi"}]))


def test_openai_compat_local_sends_reasoning_effort_none_and_strips_inline_reasoning():
    seen = []

    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "m", "choices": [{
                "finish_reason": "stop",
                "message": {"content": "<think>план</think>Ответ.", "reasoning_content": "скрыто"}}]})
        return httpx.Response(404)

    adapter = LocalOpenAICompatChat(base_url=COMPAT, transport=httpx.MockTransport(handler))
    result = asyncio.run(adapter.chat("m", [{"role": "user", "content": "hi"}], max_tokens=64))
    assert seen[0]["reasoning_effort"] == "none"
    assert result.text == "Ответ."


def test_openai_compat_local_empty_after_stripping_raises_empty_answer():
    def handler(request):
        return httpx.Response(200, json={"model": "m", "choices": [{
            "finish_reason": "stop", "message": {"content": "<think>x</think>"}}]})

    adapter = LocalOpenAICompatChat(base_url=COMPAT, transport=httpx.MockTransport(handler))
    with pytest.raises(EmptyAnswer):
        asyncio.run(adapter.chat("m", [{"role": "user", "content": "hi"}]))


def test_runtime_builds_a_no_think_adapter_for_any_local_url(tmp_path):
    for url, cls in ((NATIVE, OllamaNativeChatAdapter), (COMPAT, LocalOpenAICompatChat)):
        assert is_native_ollama_url(url) == (cls is OllamaNativeChatAdapter)
        settings = dataclasses.replace(make_settings(tmp_path / cls.__name__), local_url=url,
                                       local_models=("m",))
        runtime = make_runtime(tmp_path / cls.__name__, settings=settings)
        try:
            assert type(runtime.local_adapter) is cls
        finally:
            asyncio.run(runtime.close())


# -- runtime: empty answer recovery ---------------------------------------------------------------
class Degraded:
    """A local runner that answers empty until it is unloaded (the RC19 defect)."""

    def __init__(self, heals: bool = True):
        self.degraded, self.heals = True, heals
        self.calls, self.unloads, self.kwargs = 0, 0, []
        self.pricing = {"local:m": {"prompt": 0.0, "completion": 0.0}}

    async def chat(self, model, messages, **kw):
        self.calls += 1
        self.kwargs.append(kw)
        if self.degraded:
            raise EmptyAnswer("empty", eval_count=1)
        return ChatResult(text="Ответ после перезагрузки модели.", model=model)

    async def unload(self, model):
        self.unloads += 1
        if self.heals:
            self.degraded = False

    async def list_model_info(self):
        return [{"id": "local:m"}]


def _local_runtime(tmp_path, local):
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=(), local_url=NATIVE,
                                   local_models=("local:m",), local_chat_only=True)
    runtime = make_runtime(tmp_path, settings=settings)
    runtime.local_adapter = local
    runtime.local_settle_seconds = 0.0

    async def allowed():
        return True
    runtime.capacity_guard.local_allowed = allowed
    return runtime


def _ask(runtime, text, message_id):
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    return asyncio.run(runtime.handle(person, message(text, message_id=message_id)))


def test_empty_local_answer_is_recovered_by_one_unload_and_retry(tmp_path):
    local = Degraded()
    runtime = _local_runtime(tmp_path, local)
    try:
        answer = _ask(runtime, "Привет", 1)
        assert "после перезагрузки" in answer
        assert local.unloads == 1 and local.calls == 2
    finally:
        asyncio.run(runtime.close())


def test_unrecoverable_empty_local_answer_is_reported_honestly_and_not_sticky(tmp_path):
    local = Degraded(heals=False)
    runtime = _local_runtime(tmp_path, local)
    try:
        answer = _ask(runtime, "Привет", 1)
        assert answer in {rt.PROVIDER_DOWN_RU, rt.INCOMPLETE_REPLY_RU} and answer.strip()
        assert local.unloads == 1, "at most one unload per turn"
        local.degraded, local.heals = False, True
        assert "после перезагрузки" in _ask(runtime, "Ещё раз", 2), "a later turn is not stuck"
    finally:
        asyncio.run(runtime.close())


def test_local_chat_call_never_asks_for_reasoning(tmp_path):
    local = Degraded()
    local.degraded = False
    runtime = _local_runtime(tmp_path, local)
    try:
        _ask(runtime, "Привет", 1)
        assert all("think" not in kw and kw.get("reasoning_effort") in (None, "none")
                   for kw in local.kwargs)
    finally:
        asyncio.run(runtime.close())
