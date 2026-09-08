from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..providers import ProviderError, http_client

DEFAULT_BASE = "https://openrouter.ai/api/v1"


def normalize_base_url(url: str) -> str:
    """Адрес OpenRouter в том виде, от которого строятся пути `/key`, `/models`.

    Владелец вставляет адрес руками, и `https://openrouter.ai/api` (ровно так он
    записан в config/gateway.example.yaml, где база бэкенда обязана быть без
    версии) — самая частая форма. Клиент клеит `{base}/key`, поэтому проверка
    ключа уходила на несуществующий путь, получала 404 и превращалась в «нет
    связи с OpenRouter, повторите позже»: ключ рабочий, каталог пустой, причина
    неверная. Та же болезнь, что GATEWAY-URL-V1 в живом прогоне 20260906.

    Адрес без версии не является рабочей конфигурацией OpenRouter ни в одном
    сценарии, поэтому это нормализация, а не догадка за владельца. Явно
    указанная другая версия (`/v2`, `/openai/v1`) не трогается.
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        return DEFAULT_BASE
    if re.fullmatch(r"v\d+", base.rsplit("/", 1)[-1]):
        return base
    return base + "/v1"

def _float(v: Any, default: float | None = None) -> float | None:
    try:
        value = float(v)
        return value if not isinstance(v, bool) and math.isfinite(value) and value >= 0 else default
    except (TypeError, ValueError):
        return default

def per_million(v: Any) -> float | None:
    """OpenRouter pricing fields are commonly per-token strings; normalize to USD / 1M."""
    value = _float(v)
    if value is None or not math.isfinite(value * 1_000_000):
        return None
    return value * 1_000_000

@dataclass(slots=True)
class OpenRouterModelCard:
    id: str
    name: str
    context_length: int = 0
    price_in: float | None = None
    price_out: float | None = None
    input_modalities: list[str] = field(default_factory=list)
    output_modalities: list[str] = field(default_factory=list)
    supported_parameters: list[str] = field(default_factory=list)
    architecture: dict[str, Any] = field(default_factory=dict)
    created: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def advertised_caps(self) -> dict[str, bool]:
        params = set(self.supported_parameters)
        inp = set(self.input_modalities)
        return {
            "vision": bool({"image", "video"} & inp),
            "tools": bool({"tools", "tool_choice"} & params),
            "structured_output": bool({"response_format", "structured_outputs"} & params),
            "streaming": True,
        }

def parse_model_card(raw: dict[str, Any]) -> OpenRouterModelCard:
    arch = raw.get("architecture") or {}
    pricing = raw.get("pricing") or {}
    input_modalities = (
        arch.get("input_modalities")
        or raw.get("input_modalities")
        or []
    )
    output_modalities = (
        arch.get("output_modalities")
        or raw.get("output_modalities")
        or []
    )
    return OpenRouterModelCard(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or raw.get("id") or ""),
        context_length=int(raw.get("context_length") or 0),
        price_in=per_million(pricing.get("prompt")),
        price_out=per_million(pricing.get("completion")),
        input_modalities=[str(x) for x in input_modalities],
        output_modalities=[str(x) for x in output_modalities],
        supported_parameters=[str(x) for x in (raw.get("supported_parameters") or [])],
        architecture=dict(arch),
        created=int(raw["created"]) if raw.get("created") is not None else None,
        raw=dict(raw),
    )

def catalog_price_values(row):
    """Re-read original provider metadata so legacy zero defaults cannot become authority."""
    card = parse_model_card(row.get("raw_metadata") or {})
    return {"price_in": card.price_in, "price_out": card.price_out}


_STATUS_TEXT = {
    401: ("ключ отклонён OpenRouter (401)", "проверьте ключ на openrouter.ai/keys"),
    402: ("на счету OpenRouter не хватает средств (402)", "пополните баланс на openrouter.ai/credits"),
    403: ("ключ не допущен к этому запросу (403)", "проверьте ограничения ключа на openrouter.ai/keys"),
    404: ("OpenRouter не знает такой адрес (404)", "проверьте base_url провайдера"),
    429: ("OpenRouter ограничил частоту запросов (429)", "повторите через минуту"),
}


def explain_status(status: int) -> tuple[str, str]:
    """(причина, что делать) по коду ответа OpenRouter. Ключ в текст не попадает.

    Одно место на весь модуль: и проверка ключа, и синхронизация каталога
    обязаны называть владельцу ОДНУ и ту же причину. До этого 401 по истёкшему
    ключу (живой прогон 20260906) выходил наружу как «OpenRouter недоступен,
    повторите позже» — владелец ждал сеть вместо того, чтобы обновить ключ.
    """
    if status in _STATUS_TEXT:
        return _STATUS_TEXT[status]
    if status >= 500:
        return f"OpenRouter временно недоступен ({status})", "повторите позже"
    return f"OpenRouter ответил {status}", "повторите позже"


class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.base_url = normalize_base_url(base_url)
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        # openrouter.ai — адрес удалённый, и настроенный владельцем прокси для
        # него законен. Общий помощник оставляет прокси в силе и переводит
        # неподдержанный socks5 в обычную сетевую недоступность вместо
        # ImportError, которого здесь никто не ловил.
        return http_client(self.base_url, timeout=timeout, transport=self.transport)

    def _assert_egress(self) -> None:
        """Каталог — такой же выход наружу, как инференс.

        Раньше границу приватности проверял только chat_raw, и в приватном
        контексте запрос каталога всё равно уходил в openrouter.ai вместе с
        ключом владельца. Отказ политики поднимается PermissionError и наружу
        выходит как отказ политики, а не как пустой список.
        """
        from bossman_shared.privacy import assert_provider_egress
        assert_provider_egress("openrouter", self.base_url)

    async def validate_key(self) -> tuple[str, str]:
        """Проверка ключа без инференса: GET /key.

        Возвращает (state, detail): ok | invalid | address | network. Три
        разных действия владельца — три разных состояния: сменить ключ,
        поправить адрес, подождать сеть. Сырой ключ ни в одном сообщении
        не появляется.
        """
        self._assert_egress()
        try:
            async with self._client(15) as client:
                r = await client.get(f"{self.base_url}/key", headers=self._headers())
        except (httpx.HTTPError, ProviderError) as exc:
            # ProviderError сюда приходит от http_client: прокси из окружения
            # настроен, но не поддержан сборкой. Для владельца это та же
            # «нет связи», только с причиной, которую можно устранить.
            return "network", f"нет связи с OpenRouter: {exc}"
        if r.status_code >= 400:
            detail, hint = explain_status(r.status_code)
            # 401/403 — про ключ, 404 — про адрес, остальное — про доступность.
            # Владельцу нужно разное действие, поэтому и состояния разные.
            state = "invalid" if r.status_code in (401, 403) else (
                "address" if r.status_code == 404 else "network")
            return state, detail if state != "network" else f"{detail}; {hint}"
        return "ok", ""

    async def list_models(self) -> list[OpenRouterModelCard]:
        self._assert_egress()
        async with self._client(30) as client:
            r = await client.get(f"{self.base_url}/models", headers=self._headers())
        r.raise_for_status()
        data = r.json().get("data") or []
        return [parse_model_card(x) for x in data if x.get("id")]

    async def chat_raw(self, model: str, messages: list[dict[str, Any]], *,
                       tools: list[dict[str, Any]] | None = None,
                       tool_choice: Any = None,
                       response_format: dict[str, Any] | None = None,
                       max_tokens: int = 128,
                       temperature: float | None = None,
                       stream: bool = False,
                       provider: dict[str, Any] | None = None) -> dict[str, Any]:
        from bossman_shared.privacy import assert_provider_egress
        assert_provider_egress("openrouter", self.base_url)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature
        if provider is not None:
            payload["provider"] = provider
        async with self._client(120) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
        r.raise_for_status()
        return r.json()

    async def stream_outcome(self, model: str, messages: list[dict[str, Any]], *,
                             max_tokens: int = 32, temperature: float | None = 0,
                             max_chunks: int = 32,
                             first_byte_timeout: float | None = None,
                             total_timeout: float | None = None):
        """SSE-стрим → StreamOutcome (см. bcc/streaming).

        Разбор кадров вынесен в один канонический модуль: прежний читатель
        принимал ровно `choices[0].delta.content`, поэтому рассуждающая модель
        (GLM 5.3 кладёт текст в `delta.reasoning`) давала «0 chunks» и
        записывалась как «не умеет стримить». Здесь остаётся только транспорт:
        таймауты, заголовки и один проход по строкам."""
        from ..streaming import (DEFAULT_FIRST_BYTE_TIMEOUT, DEFAULT_TOTAL_TIMEOUT,
                                 outcome_from_exception, read_stream)
        from bossman_shared.privacy import assert_provider_egress
        assert_provider_egress("openrouter", self.base_url)
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "max_tokens": max_tokens, "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        first = DEFAULT_FIRST_BYTE_TIMEOUT if first_byte_timeout is None else first_byte_timeout
        total = DEFAULT_TOTAL_TIMEOUT if total_timeout is None else total_timeout
        # `read` — это и есть бюджет «первого байта»: провайдер, который принял
        # соединение и замолчал, обязан оборваться раньше общего таймаута.
        timeout = httpx.Timeout(total, connect=min(30.0, total), read=first)
        try:
            # Тот же помощник, что и у остальных вызовов: он решает вопрос
            # прокси и не теряет transport, подставленный тестом.
            async with http_client(self.base_url, timeout=timeout,
                                   transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions",
                                         headers=self._headers(), json=payload) as r:
                    if r.status_code >= 400:
                        # Тело ошибки читаем целиком: 429/402/5xx — про
                        # провайдера, а не про способность модели стримить.
                        body = (await r.aread()).decode("utf-8", "replace")[:300]
                        from ..streaming import PROVIDER_FAILED, StreamOutcome
                        return StreamOutcome(status=PROVIDER_FAILED,
                                             error=f"HTTP {r.status_code}: {body}",
                                             detail="provider refused the stream")
                    return await read_stream(r.aiter_lines(), max_chunks=max_chunks)
        except Exception as exc:  # noqa: BLE001 — транспорт классифицируется, а не глотается
            return outcome_from_exception(exc)

    async def stream_raw(self, model: str, messages: list[dict[str, Any]], *,
                         max_tokens: int = 32, temperature: float | None = 0,
                         max_chunks: int = 32) -> list[str]:
        """Обратно совместимая обёртка: список текстовых дельт.

        Пустой список по-прежнему значит «полезного текста не пришло», но теперь
        это решает канонический разбор, а не одна ветка `delta.content`."""
        outcome = await self.stream_outcome(model, messages, max_tokens=max_tokens,
                                            temperature=temperature, max_chunks=max_chunks)
        return list(outcome.deltas)

    async def probe_chat(self, model: str) -> tuple[bool, str]:
        try:
            data = await self.chat_raw(
                model, [{"role": "user", "content": "Reply exactly OK"}],
                max_tokens=8, temperature=0
            )
            text = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
            return True, text[:120]
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    async def probe_tools(self, model: str) -> tuple[bool, str]:
        tool = {
            "type": "function",
            "function": {
                "name": "bossman_probe",
                "description": "Return the supplied integer.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            },
        }
        try:
            data = await self.chat_raw(
                model,
                [{"role": "user", "content": "Call bossman_probe with value 7. Do not answer normally."}],
                tools=[tool],
                tool_choice="auto",
                max_tokens=64,
                temperature=0,
            )
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            calls = msg.get("tool_calls") or []
            return bool(calls), json.dumps(calls[:1])[:500]
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
