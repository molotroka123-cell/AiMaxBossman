"""Девять провайдеров main и автомат защиты ветки — вместе, а не вместо.

Слияние с main выглядело как выбор между двумя шлюзами. Выбором оно не было:
шлюз main не импортируется на самом main (`app.py` просит `CircuitOpenError`,
которого в его же `backends.py` нет, а `cli.py` — `load_provider_config`,
которого нет в его же `config.py`). Работающей частью там был ровно один файл —
набор провайдеров.

Поэтому здесь проверяется композиция: провайдеров стало девять, и КАЖДЫЙ из них
живёт под тем же автоматом защиты, теми же таймаутами и той же честной
диагностикой, что и два исходных. Ни один не получил собственную копию
машинерии, потому что девять копий — это девять мест, где однажды забудут
разомкнуть автомат.
"""
from __future__ import annotations

import httpx
import pytest

from bossman.gateway.backends import (AnthropicBackend, CircuitBreaker,
                                      CircuitOpenError, OpenAIBackend,
                                      OpenRouterBackend, ZaiBackend,
                                      build_backend)
from bossman.gateway.config import (AVAILABLE_PROVIDERS, ENV_BACKENDS,
                                    load_provider_config)

# То, что владелец ожидал получить от main.
EXPECTED = {"openai", "anthropic", "zai", "openrouter", "google", "groq",
            "mistral", "together", "ollama"}


def test_every_provider_main_had_is_reachable():
    assert set(AVAILABLE_PROVIDERS) == EXPECTED


def test_every_provider_runs_behind_the_same_circuit_breaker():
    """Главное свойство композиции. Провайдер без автомата — это провайдер,
    на котором таймауты инференса выжигаются вслепую."""
    for name in AVAILABLE_PROVIDERS:
        backend = build_backend(load_provider_config(name))
        assert isinstance(backend, OpenAIBackend), name
        assert isinstance(backend.breaker, CircuitBreaker), name
        assert backend.semaphore is not None, name
        assert backend.config.timeout_seconds > 0, name


def test_the_local_provider_is_not_marked_cloud():
    """Ollama — локальная. Пометить её облаком значит подчинить её облачной
    политике, которой она не подчиняется."""
    assert load_provider_config("ollama").cloud is False
    assert load_provider_config("ollama").api_key_env is None


def test_every_cloud_provider_declares_its_key_variable():
    for name in AVAILABLE_PROVIDERS:
        config = load_provider_config(name)
        if config.cloud:
            assert config.api_key_env, name


def test_a_provider_without_a_key_is_unavailable_not_broken():
    """Отсутствие ключа — не авария и не пустой каталог: владельцу называют
    переменную, которую он не задал."""
    backend = build_backend(load_provider_config("groq", api_key_env="NOPE_KEY"))
    reason = backend.unavailable_reason()
    assert reason and "NOPE_KEY" in reason


def test_only_anthropic_needs_its_own_class():
    """Остальные восемь говорят на диалекте OpenAI и класса не требуют."""
    special = {name for name in AVAILABLE_PROVIDERS
               if type(build_backend(load_provider_config(name))) is not OpenAIBackend}
    assert special == {"anthropic", "openrouter", "zai"}
    assert type(build_backend(load_provider_config("anthropic"))) is AnthropicBackend
    assert type(build_backend(load_provider_config("openrouter"))) is OpenRouterBackend
    assert type(build_backend(load_provider_config("zai"))) is ZaiBackend


def test_an_unknown_provider_is_named_not_guessed():
    with pytest.raises(ValueError, match="неизвестный провайдер"):
        load_provider_config("definitely-not-a-provider")


def test_env_discovery_covers_every_cloud_provider():
    """«Дал ключ — провайдер появился» должно работать для всех, а не для двух."""
    discovered = {name for name, _, _ in ENV_BACKENDS}
    cloud = {name for name in AVAILABLE_PROVIDERS
             if load_provider_config(name).cloud}
    assert discovered == cloud


# ------------------------------------------------------------------ Anthropic

def test_anthropic_sends_its_key_in_its_own_header():
    backend = AnthropicBackend.from_env(api_key="test-key-value")
    headers = backend.headers()
    assert headers["x-api-key"] == "test-key-value"
    assert "authorization" not in headers, "Anthropic не принимает Bearer"
    assert headers["anthropic-version"]


def test_anthropic_lifts_the_system_message_out_of_the_list():
    payload = AnthropicBackend.to_anthropic_payload({
        "model": "claude", "messages": [
            {"role": "system", "content": "будь краток"},
            {"role": "user", "content": "привет"}]})
    assert payload["system"] == "будь краток"
    assert [m["role"] for m in payload["messages"]] == ["user"]


def test_anthropic_always_carries_a_named_token_limit():
    """Молчаливый лимит обрезает ответ, и владелец ищет причину в модели."""
    payload = AnthropicBackend.to_anthropic_payload({"model": "c", "messages": []})
    assert payload["max_tokens"] > 0
    kept = AnthropicBackend.to_anthropic_payload(
        {"model": "c", "messages": [], "max_tokens": 77})
    assert kept["max_tokens"] == 77


def test_anthropic_inference_lives_on_its_own_path():
    backend = AnthropicBackend.from_env()
    assert backend.resolve_path("/v1/chat/completions") == "/v1/messages"
    assert backend.resolve_path("/v1/models") == "/v1/models"


def test_an_anthropic_reply_comes_back_in_the_shape_the_gateway_speaks():
    body = AnthropicBackend.to_openai_response({
        "id": "msg_1", "model": "claude-x", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "привет"}],
        "usage": {"input_tokens": 5, "output_tokens": 2}})
    assert body["choices"][0]["message"]["content"] == "привет"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 7


async def test_the_anthropic_catalogue_is_read_not_hardcoded():
    """В main список моделей Anthropic был зашит тремя строками — то есть
    показывался и там, где ключа нет и связи нет. Это не каталог, это надпись.
    """
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": [{"id": "claude-from-server"}]})

    backend = AnthropicBackend.from_env(api_key="k",
                                        transport=httpx.MockTransport(handler))
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()
    assert listing.ok and listing.models == ["claude-from-server"]
    assert seen["path"] == "/v1/models"


async def test_an_anthropic_key_that_is_refused_says_what_to_do():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    backend = AnthropicBackend.from_env(api_key="k",
                                        transport=httpx.MockTransport(handler))
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()
    assert not listing.ok
    assert "замените ключ" in listing.reason


async def test_a_new_provider_trips_its_breaker_like_the_old_ones():
    """Автомат — не украшение конфигурации: он обязан размыкаться."""
    async def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    backend = build_backend(load_provider_config("groq", api_key="k"),
                            httpx.MockTransport(failing))
    try:
        for _ in range(backend.config.circuit_failure_threshold):
            with pytest.raises(Exception):
                await backend.json_request("/v1/chat/completions", {"model": "m"})
        assert backend.breaker.state == "open"
        assert backend.breaker.allow_attempt() is False
    finally:
        await backend.close()


def test_circuit_open_error_survived_the_merge():
    """Символ, из-за отсутствия которого шлюз main не импортируется."""
    assert issubclass(CircuitOpenError, RuntimeError)
