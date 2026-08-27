"""Провайдер-слой (раздел 2): единый интерфейс + адаптеры openai_compat и anthropic.

Новый провайдер = один класс с chat/health/list_models, зарегистрированный в ADAPTERS.
Ошибки наружу — человекочитаемые (ProviderError), без ключей и без стек-трейсов.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

CHAT_TIMEOUT = 600.0     # локальная модель на CPU думает долго
HEALTH_TIMEOUT = 6.0     # проверка доступности должна быть быстрой


class ProviderError(RuntimeError):
    """Понятная человеку ошибка провайдера: показывается в UI как есть.

    kind: network — до endpoint'а не достучались; http — ответил, но отказом.
    """

    def __init__(self, message: str, *, kind: str = "http", hint: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.hint = hint


@dataclass
class ChatResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish: str = "stop"
    model: str = ""

    @property
    def usage(self) -> dict[str, int]:
        return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out}


@dataclass
class Health:
    status: str = "unknown"          # ok | offline | error
    detail: str = ""
    latency_ms: int | None = None


class ProviderAdapter(Protocol):
    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult: ...
    async def health(self) -> Health: ...
    async def list_models(self) -> list[str]: ...


@dataclass
class _BaseAdapter:
    base_url: str = ""
    api_key: str | None = None
    transport: Any = None            # httpx.MockTransport в тестах

    # без аннотации: это не поле dataclass, а константа класса-адаптера
    default_base = ""

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or self.default_base).rstrip("/")

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    async def _request(self, method: str, url: str, *, timeout: float,
                       headers: dict | None = None, json: dict | None = None) -> httpx.Response:
        try:
            async with self._client(timeout) as client:
                resp = await client.request(method, url, headers=headers, json=json)
        except httpx.TimeoutException:
            raise ProviderError(f"{_host(url)} не ответил за {int(timeout)} с", kind="network",
                                hint="проверьте, что сервер модели запущен") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"нет связи с {_host(url)}: {type(exc).__name__}", kind="network",
                                hint="проверьте base_url и что endpoint поднят") from None
        if resp.status_code >= 400:
            raise ProviderError(_explain(resp), kind="http")
        return resp

    async def _health_via(self, url: str, headers: dict | None = None) -> Health:
        t0 = time.perf_counter()
        try:
            await self._request("GET", url, timeout=HEALTH_TIMEOUT, headers=headers)
        except ProviderError as exc:
            return Health(status="offline" if exc.kind == "network" else "error", detail=str(exc))
        return Health(status="ok", detail="", latency_ms=int((time.perf_counter() - t0) * 1000))


class OpenAICompatAdapter(_BaseAdapter):
    """Любой OpenAI-совместимый endpoint: llama.cpp, Ollama, vLLM, LM Studio, LiteLLM, OpenRouter."""

    kind = "openai_compat"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        for key in ("max_tokens", "temperature", "top_p", "stop"):
            if kw.get(key) is not None:
                payload[key] = kw[key]
        resp = await self._request("POST", f"{self.base_url}/chat/completions",
                                   timeout=kw.get("timeout", CHAT_TIMEOUT),
                                   headers=self._headers(), json=payload)
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("модель вернула пустой ответ (нет choices)")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        return ChatResult(
            text=(message.get("content") or "").strip(),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            finish=choices[0].get("finish_reason") or "stop",
            model=data.get("model") or model,
        )

    async def health(self) -> Health:
        return await self._health_via(f"{self.base_url}/models", self._headers())

    async def list_models(self) -> list[str]:
        resp = await self._request("GET", f"{self.base_url}/models", timeout=HEALTH_TIMEOUT,
                                   headers=self._headers())
        data = resp.json().get("data") or []
        return [str(m.get("id")) for m in data if m.get("id")]


class AnthropicAdapter(_BaseAdapter):
    """Облачный адаптер: Anthropic Messages API v1."""

    kind = "anthropic"
    version = "2023-06-01"
    default_base = "https://api.anthropic.com"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": self.version,
        }

    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult:
        if not self.api_key:
            raise ProviderError("для Anthropic нужен api_key", hint="добавьте ключ в провайдере")
        # system у Anthropic — отдельное поле, а не роль в messages
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        chat_messages = [{"role": m["role"], "content": m["content"]}
                         for m in messages if m.get("role") in ("user", "assistant")]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(kw.get("max_tokens") or 2048),
            "messages": chat_messages,
        }
        if system:
            payload["system"] = system
        if kw.get("temperature") is not None:
            payload["temperature"] = kw["temperature"]
        resp = await self._request("POST", f"{self.base_url}/v1/messages",
                                   timeout=kw.get("timeout", CHAT_TIMEOUT),
                                   headers=self._headers(), json=payload)
        data = resp.json()
        text = "".join(block.get("text", "") for block in (data.get("content") or [])
                       if block.get("type") == "text").strip()
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
            finish=data.get("stop_reason") or "stop",
            model=data.get("model") or model,
        )

    async def health(self) -> Health:
        if not self.api_key:
            return Health(status="error", detail="не задан api_key")
        return await self._health_via(f"{self.base_url}/v1/models", self._headers())

    async def list_models(self) -> list[str]:
        resp = await self._request("GET", f"{self.base_url}/v1/models", timeout=HEALTH_TIMEOUT,
                                   headers=self._headers())
        data = resp.json().get("data") or []
        return [str(m.get("id")) for m in data if m.get("id")]


ADAPTERS: dict[str, type[_BaseAdapter]] = {
    "openai_compat": OpenAICompatAdapter,
    "anthropic": AnthropicAdapter,
}


def build_adapter(kind: str, base_url: str = "", api_key: str | None = None,
                  transport: Any = None) -> ProviderAdapter:
    cls = ADAPTERS.get(kind)
    if cls is None:
        raise ProviderError(f"неизвестный вид провайдера: {kind}",
                            hint=f"доступны: {', '.join(ADAPTERS)}")
    return cls(base_url=base_url, api_key=api_key, transport=transport)  # type: ignore[return-value]


def _host(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return f"{parsed.scheme}://{parsed.netloc.decode()}"
    except Exception:
        return url


def _explain(resp: httpx.Response) -> str:
    """HTTP-ошибка провайдера словами человека (без секретов — тело ответа их не содержит)."""
    code = resp.status_code
    body = (resp.text or "").strip().replace("\n", " ")[:200]
    known = {
        400: "провайдер отклонил запрос (400)",
        401: "ключ отклонён (401): проверьте api_key",
        403: "доступ запрещён (403): ключ без прав на эту модель",
        404: f"endpoint не найден (404): {_host(str(resp.request.url))} — проверьте base_url",
        408: "провайдер не успел ответить (408)",
        413: "запрос слишком большой (413): уменьшите контекст",
        429: "лимит запросов провайдера (429): попробуйте позже",
    }
    head = known.get(code) or (f"сервер провайдера ответил {code}" if code >= 500
                               else f"провайдер ответил {code}")
    return f"{head}: {body}" if body else head
