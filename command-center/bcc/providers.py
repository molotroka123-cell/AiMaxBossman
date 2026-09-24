"""Провайдер-слой (раздел 2): единый интерфейс + адаптеры openai_compat и anthropic.

Новый провайдер = один класс с chat/health/list_models, зарегистрированный в ADAPTERS.
Ошибки наружу — человекочитаемые (ProviderError), без ключей и без стек-трейсов.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from urllib.parse import urlparse

CHAT_TIMEOUT = 600.0     # локальная модель на CPU думает долго
HEALTH_TIMEOUT = 6.0     # проверка доступности должна быть быстрой
# OpenRouter (и другие шлюзы) отвечают HTTP 200 с телом {"error": {"code": 503, ...}},
# когда апстрим перегружен. Это временный отказ: две короткие повторные попытки,
# затем названная ошибка с kind="rate_limit" (backoff), а не «модель молчит».
TRANSIENT_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)
_TRANSIENT_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})


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
    # Prompt cache (Anthropic): измеренные провайдером токены, не оценка.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def usage(self) -> dict[str, int]:
        out = {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out}
        if self.cache_read_tokens or self.cache_write_tokens:   # только измеренный кэш
            out["cache_read_tokens"] = self.cache_read_tokens
            out["cache_write_tokens"] = self.cache_write_tokens
        return out

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


def _response_object(resp: httpx.Response, *, what: str) -> dict[str, Any]:
    """Decode a provider response into an object or fail as a provider error.

    A HTTP 200 body that is HTML/truncated JSON is a protocol failure, not an
    unhandled Python exception. Letting JSONDecodeError escape left the run
    `running` until lease recovery and hid the actual provider cause.
    """
    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        raise ProviderError(f"{what}: сервер вернул невалидный JSON", kind="protocol",
                            hint="проверьте совместимость endpoint и формат ответа") from None
    if not isinstance(data, dict):
        raise ProviderError(f"{what}: ожидался JSON-объект, получен {type(data).__name__}",
                            kind="protocol")
    return data


@dataclass
class Health:
    status: str = "unknown"          # ok | offline | error
    detail: str = ""
    latency_ms: int | None = None


class ProviderAdapter(Protocol):
    async def chat(self, model: str, messages: list[dict], **kw: Any) -> ChatResult: ...
    async def health(self) -> Health: ...
    async def list_models(self) -> list[str]: ...


def is_local_url(url: str) -> bool:
    """Адрес на этой же машине или в локальной сети.

    Список намеренно широкий: `localhost`, петля, `.local`, приватные диапазоны
    RFC1918 и `host.docker.internal`. Ошибиться в сторону «не проксировать»
    безопасно — прокси для локального адреса не нужен никогда.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"):
        return True
    if host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class ProxyUnsupported(ProviderError):
    """Прокси из окружения настроен, но этот httpx его не умеет.

    Отдельный класс, а не голый ImportError: наружу это обязано выйти честным
    «недоступно» с внятной причиной, а не пятисоткой из ниоткуда.
    """

    def __init__(self, url: str, detail: str):
        super().__init__(
            f"прокси из окружения не поддержан этой сборкой, к {_host(url)} не идём: {detail}",
            kind="network",
            hint="для socks5 нужен httpx[socks] (пакет socksio); "
                 "либо уберите ALL_PROXY/HTTPS_PROXY для этого адреса")


def http_client(url: str, *, timeout: float, transport: Any = None,
                **kw: Any) -> httpx.AsyncClient:
    """Единственное место, где решается, идти ли через прокси из окружения.

    Правило одно на весь проект и держится на `is_local_url`, а не на своей
    копии условий: локальный адрес НИКОГДА не ходит через прокси. httpx по
    умолчанию читает HTTP_PROXY/HTTPS_PROXY/ALL_PROXY, и запрос к
    127.0.0.1:11434 уходил бы на прокси, который про этот адрес ничего не
    знает: соединение висит до таймаута, а владелец видит «локальная модель не
    отвечает» при работающей модели. Диагноз получается ложный.

    Глобальный `trust_env=False` эту беду тоже убрал бы — и заодно выключил бы
    прокси владельца там, где он настроен намеренно. Поэтому удалённые адреса
    прокси по-прежнему видят.

    Вторая забота — SOCKS. socks5-прокси httpx умеет только с пакетом socksio,
    и без него падает ПРИ СОЗДАНИИ клиента: ImportError, которого не ждёт ни
    один вызывающий, — наружу он выходил пятисоткой на здоровой в остальном
    системе. Теперь это ProxyUnsupported, то есть обычная сетевая
    недоступность с объяснением, что доустановить.
    """
    try:
        return httpx.AsyncClient(timeout=timeout, transport=transport,
                                 trust_env=not is_local_url(url), **kw)
    except ImportError as exc:
        raise ProxyUnsupported(url, str(exc)) from exc


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
        return http_client(self.base_url, timeout=timeout, transport=self.transport)

    async def _request(self, method: str, url: str, *, timeout: float,
                       headers: dict | None = None, json: dict | None = None) -> httpx.Response:
        from bossman_shared.privacy import assert_provider_egress
        assert_provider_egress(self.kind, url)
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
        delays = iter(TRANSIENT_RETRY_DELAYS)
        while True:
            resp = await self._request("POST", f"{self.base_url}/chat/completions",
                                       timeout=kw.get("timeout", CHAT_TIMEOUT),
                                       headers=self._headers(), json=payload)
            data = _response_object(resp, what="chat/completions")
            upstream = _in_body_error(data)
            if upstream is None:
                break
            code, message = upstream
            if code not in _TRANSIENT_CODES:
                raise ProviderError(f"провайдер отказал ({code}): {message}", kind="http")
            delay = next(delays, None)
            if delay is None:
                raise ProviderError(f"провайдер временно перегружен ({code}): {message}",
                                    kind="rate_limit", hint="повторите позже или выберите другую модель")
            await asyncio.sleep(delay)
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
            # timings: llama.cpp отдаёт собственные замеры prefill/генерации —
            # по ним честная скорость (TEL-001), а не токены / вся латентность.
            provider_meta={k: data[k] for k in ("id", "provider", "usage", "timings") if k in data},
        )

    async def health(self) -> Health:
        t0 = time.perf_counter()
        try:
            models = await self.list_model_info()
        except ProviderError as exc:
            return Health(status="offline" if exc.kind == "network" else "error", detail=str(exc))
        detail = model_catalog_problem(models)
        return Health(status="error" if detail else "ok", detail=detail,
                      latency_ms=int((time.perf_counter() - t0) * 1000))

    async def list_models(self) -> list[str]:
        return [m["id"] for m in await self.list_model_info()]

    async def list_model_info(self) -> list[dict[str, Any]]:
        """Read the existing catalog without loading a model or issuing inference.

        llama.cpp reports a null meta while loading and per-model state in
        router mode. Retain that evidence; a successful catalog HTTP request
        alone does not prove model readiness. Only public, selected fields are
        returned (router launch arguments can contain credentials).
        """
        resp = await self._request("GET", f"{self.base_url}/models", timeout=HEALTH_TIMEOUT,
                                   headers=self._headers())
        data = _response_object(resp, what="models").get("data")
        if not isinstance(data, list):
            raise ProviderError("models: поле data должно быть списком", kind="protocol")
        models: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ProviderError("models: у модели отсутствует строковый id", kind="protocol")
            model: dict[str, Any] = {"id": item["id"]}
            owner = item.get("owned_by")
            if isinstance(owner, str):
                model["owned_by"] = owner
            # These are training/model facts, not measured inference capacity.
            meta = item.get("meta")
            if isinstance(meta, dict):
                model["meta"] = {key: value for key in ("n_ctx_train", "n_params", "size")
                                 if isinstance((value := meta.get(key)), int)
                                 and not isinstance(value, bool) and value >= 0}
            status = item.get("status")
            if isinstance(status, dict):
                value = status.get("value")
                if value in ("loaded", "loading", "unloaded", "sleeping", "downloading"):
                    model["state"] = value
                if status.get("failed") is True:
                    model["state"] = "failed"
            elif owner == "llamacpp" and "meta" in item and meta is None:
                model["state"] = "loading"
            models.append(model)
        return models


def model_catalog_problem(models: list[dict[str, Any]]) -> str:
    """Catalog availability, not a claim that a real inference has passed."""
    if not models:
        return "сервер доступен, но список моделей пуст: загрузите или настройте модель"
    if all(m.get("state") in ("loading", "downloading", "failed") for m in models):
        if any(m.get("state") in ("loading", "downloading") for m in models):
            return "llama.cpp загружает модель; дождитесь окончания загрузки"
        return "llama.cpp не смог загрузить модель: проверьте журнал сервера и путь к GGUF"
    return ""


class AnthropicAdapter(_BaseAdapter):
    """Облачный адаптер: Anthropic Messages API v1."""

    kind = "anthropic"
    version = "2023-06-01"
    default_base = "https://api.anthropic.com"
    # Prompt caching (Anthropic Messages API): стабильный префикс — system и
    # tools — помечается cache_control; динамика (messages) идёт ПОСЛЕ него.
    # BCC_ANTHROPIC_PROMPT_CACHE=0 выключает; BCC_ANTHROPIC_CACHE_TTL=1h — длинный TTL.
    # Экономия не декларируется: usage.cache_read_input_tokens/
    # cache_creation_input_tokens возвращаются в ChatResult как измерение.

    @staticmethod
    def cache_policy() -> dict | None:
        if os.environ.get("BCC_ANTHROPIC_PROMPT_CACHE", "1").strip().lower() in ("0", "false", "no"):
            return None
        control = {"type": "ephemeral"}
        if os.environ.get("BCC_ANTHROPIC_CACHE_TTL", "5m").strip().lower() == "1h":
            control["ttl"] = "1h"
        return control

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
        control = self.cache_policy()
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(kw.get("max_tokens") or 2048),
        }
        if kw.get("tools"):
            # OpenAI-схемы инструментов → формат Anthropic (плоский, input_schema).
            # tools — часть стабильного префикса: breakpoint на последнем инструменте.
            tools = [{
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters")
                or {"type": "object", "properties": {}},
            } for t in kw["tools"] if t.get("function")]
            if tools and control:
                tools[-1] = {**tools[-1], "cache_control": dict(control)}
            payload["tools"] = tools
        if system:
            # system — стабильный префикс; блок с cache_control (кэшируется вместе с tools)
            payload["system"] = ([{"type": "text", "text": system, "cache_control": dict(control)}]
                                 if control else system)
        payload["messages"] = _to_anthropic_messages(messages)   # динамика — после префикса
        if kw.get("temperature") is not None:
            payload["temperature"] = kw["temperature"]
        resp = await self._request("POST", f"{self.base_url}/v1/messages",
                                   timeout=kw.get("timeout", CHAT_TIMEOUT),
                                   headers=self._headers(), json=payload)
        data = _response_object(resp, what="Anthropic messages")
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        calls = [ToolCall(id=str(b.get("id") or f"call_{i}"),
                          name=str(b.get("name") or ""),
                          arguments=dict(b.get("input") or {}),
                          raw_arguments=json.dumps(b.get("input") or {}, ensure_ascii=False))
                 for i, b in enumerate(blocks) if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        meta = {k: data[k] for k in ("id", "usage") if k in data}
        meta["prompt_cache"] = {"applied": bool(control), "read_tokens": cache_read,
                                "write_tokens": cache_write,
                                "hit": cache_read > 0}
        return ChatResult(
            text=text,
            # input_tokens у Anthropic НЕ включает кэшированные — полный вход = сумма
            tokens_in=int(usage.get("input_tokens") or 0) + cache_read + cache_write,
            tokens_out=int(usage.get("output_tokens") or 0),
            finish=data.get("stop_reason") or "stop",
            model=data.get("model") or model,
            tool_calls=calls,
            raw_message={"content": blocks},
            provider_meta=meta,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        )

    async def health(self) -> Health:
        if not self.api_key:
            return Health(status="error", detail="не задан api_key")
        return await self._health_via(f"{self.base_url}/v1/models", self._headers())

    async def list_models(self) -> list[str]:
        resp = await self._request("GET", f"{self.base_url}/v1/models", timeout=HEALTH_TIMEOUT,
                                   headers=self._headers())
        data = _response_object(resp, what="Anthropic models").get("data") or []
        if not isinstance(data, list):
            raise ProviderError("Anthropic models: поле data должно быть списком", kind="protocol")
        return [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")]


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


def _in_body_error(data: dict) -> tuple[int, str] | None:
    """(code, message) of an error a gateway put into a 200 body, else None."""
    err = data.get("error") if isinstance(data, dict) else None
    if not err or data.get("choices"):
        return None
    if isinstance(err, dict):
        try:
            code = int(err.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        return code, str(err.get("message") or err)[:300]
    return 0, str(err)[:300]


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
