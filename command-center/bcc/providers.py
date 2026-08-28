"""Провайдер-слой (раздел 2): единый интерфейс + адаптеры openai_compat и anthropic.

Новый провайдер = один класс с chat/health/list_models, зарегистрированный в ADAPTERS.
Ошибки наружу — человекочитаемые (ProviderError), без ключей и без стек-трейсов.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
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
class ToolCall:
    """Вызов инструмента, как его вернула модель. Сохраняется целиком:
    id нужен, чтобы вернуть результат тем же tool-сообщением."""
    id: str
    name: str                       # имя в схеме модели (api_name)
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""         # сырой JSON провайдера — как пришёл


@dataclass
class ChatResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish: str = "stop"
    model: str = ""
    # V2.1: ответ модели с инструментами не схлопывается в текст
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    provider_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def usage(self) -> dict[str, int]:
        return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out}

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _parse_tool_arguments(raw: Any) -> tuple[dict[str, Any], str]:
    """Аргументы приходят строкой JSON (OpenAI) или объектом (Anthropic).
    Кривой JSON не роняет run: отдаём {"_raw": …} — модель увидит ошибку."""
    if isinstance(raw, dict):
        return dict(raw), json.dumps(raw, ensure_ascii=False)
    text = str(raw or "{}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}, text
    if not isinstance(parsed, dict):
        return {"value": parsed}, text
    return parsed, text


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
        if kw.get("tools"):
            payload["tools"] = kw["tools"]
            payload["tool_choice"] = kw.get("tool_choice") or "auto"
        if kw.get("response_format") is not None:
            payload["response_format"] = kw["response_format"]
        resp = await self._request("POST", f"{self.base_url}/chat/completions",
                                   timeout=kw.get("timeout", CHAT_TIMEOUT),
                                   headers=self._headers(), json=payload)
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("модель вернула пустой ответ (нет choices)")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            fn = item.get("function") or {}
            args, raw = _parse_tool_arguments(fn.get("arguments"))
            calls.append(ToolCall(id=str(item.get("id") or f"call_{len(calls)}"),
                                  name=str(fn.get("name") or ""),
                                  arguments=args, raw_arguments=raw))
        return ChatResult(
            text=(message.get("content") or "").strip(),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            finish=choices[0].get("finish_reason") or "stop",
            model=data.get("model") or model,
            tool_calls=calls,
            raw_message=dict(message),
            provider_meta={k: data[k] for k in ("id", "provider", "usage") if k in data},
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
        system = "\n\n".join(str(m.get("content") or "")
                             for m in messages if m.get("role") == "system")
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(kw.get("max_tokens") or 2048),
            "messages": _to_anthropic_messages(messages),
        }
        if system:
            payload["system"] = system
        if kw.get("temperature") is not None:
            payload["temperature"] = kw["temperature"]
        if kw.get("tools"):
            # OpenAI-схемы инструментов → формат Anthropic (плоский, input_schema)
            payload["tools"] = [{
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters")
                or {"type": "object", "properties": {}},
            } for t in kw["tools"] if t.get("function")]
        resp = await self._request("POST", f"{self.base_url}/v1/messages",
                                   timeout=kw.get("timeout", CHAT_TIMEOUT),
                                   headers=self._headers(), json=payload)
        data = resp.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        calls = [ToolCall(id=str(b.get("id") or f"call_{i}"),
                          name=str(b.get("name") or ""),
                          arguments=dict(b.get("input") or {}),
                          raw_arguments=json.dumps(b.get("input") or {}, ensure_ascii=False))
                 for i, b in enumerate(blocks) if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
            finish=data.get("stop_reason") or "stop",
            model=data.get("model") or model,
            tool_calls=calls,
            raw_message={"content": blocks},
            provider_meta={k: data[k] for k in ("id", "usage") if k in data},
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


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """История движка (OpenAI-стиль) → блоки Anthropic.

    assistant с tool_calls → content-блоки tool_use; сообщения role=tool →
    user-сообщение с блоками tool_result (Anthropic не знает такой роли).
    Подряд идущие tool-результаты склеиваются в одно user-сообщение — так
    требует API.
    """
    out: list[dict] = []
    pending_results: list[dict] = []

    def flush() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": str(m.get("tool_call_id") or ""),
                "content": str(m.get("content") or ""),
            })
            continue
        flush()
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": str(m["content"])})
            for call in m["tool_calls"]:
                fn = call.get("function") or {}
                args, _ = _parse_tool_arguments(fn.get("arguments"))
                blocks.append({"type": "tool_use", "id": str(call.get("id") or ""),
                               "name": str(fn.get("name") or ""), "input": args})
            out.append({"role": "assistant", "content": blocks})
        elif role in ("user", "assistant"):
            out.append({"role": role, "content": str(m.get("content") or "")})
    flush()
    return out


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
