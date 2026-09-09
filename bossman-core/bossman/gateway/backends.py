from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from . import anthropic_protocol
from .config import (ANTHROPIC_VERSION, BackendConfig, anthropic_backend_config,
                     openrouter_backend_config, zai_backend_config)


# 4xx, при которых переключение на следующий таргет оправдано (бэкенд занят/
# таймаут), в отличие от 400/401/403/404/422 — ошибок самого запроса/политики,
# которые дал бы любой таргет.
_FAILOVER_4XX = {408, 425, 429}

# Anthropic требует max_tokens в каждом запросе. Значение по умолчанию названо
# здесь, а не подставлено молча в транспорте: молчаливый лимит обрезает ответ,
# и владелец ищет причину в модели.
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096

# A read timeout alone permits an upstream to send heartbeats forever. Bound
# total wall time by the configured request timeout and wire volume by 32 MiB.
# Content is still streamed; this is not a 32 MiB accumulation buffer.
MAX_STREAM_BYTES = 32 * 1024 * 1024


class BackendError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None,
                 response_started: bool = False):
        super().__init__(message)
        self.status_code = status_code
        # A successful upstream HTTP response may already be billable even if
        # its payload is corrupt. The budget must not refund that attempt.
        self.response_started = response_started

    @property
    def failover(self) -> bool:
        """Стоит ли пробовать следующий таргет. 4xx запроса/политики — НЕТ
        (тот же ответ дал бы любой бэкенд; эскалация на облако недопустима, и
        здоровье бэкенда гасить нельзя). 5xx / нет ответа / транспорт — ДА."""
        if self.status_code is None:
            return True
        if self.status_code >= 500:
            return True
        return self.status_code in _FAILOVER_4XX


class CircuitOpenError(RuntimeError):
    """Все подходящие цели алиаса разомкнуты автоматом.

    Отдаём отказ сразу (503), не выжигая таймауты инференса на заведомо
    мёртвых бэкендах: клиент быстрее получит ошибку и сможет уйти сам."""


class CircuitBreaker:
    """Circuit breaker на бэкенд.

    CLOSED → N неудач подряд (транспорт/таймаут/5xx) → OPEN на cooldown
    секунд → HALF_OPEN: одна пробная попытка; успех закрывает автомат,
    провал переоткрывает на новый cooldown. Обычные клиентские 4xx автомат
    не двигают — они говорят о запросе, а не о бэкенде."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0,
                 request_timeout_seconds: float = 120.0):
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        # Пробная HALF_OPEN попытка считается завершившейся по истечении
        # таймаута запроса — страховка от потерянного record_* (утечки флага).
        self.request_timeout_seconds = max(0.1, float(request_timeout_seconds))
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self.last_reason: str | None = None
        self._half_open_granted_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None or self.consecutive_failures < self.failure_threshold:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    def allow_attempt(self) -> bool:
        """Пускать ли запрос на бэкенд. В HALF_OPEN разрешает ровно одну
        пробную попытку за раз; повторные запросы ждут её исхода."""
        st = self.state
        if st == "closed":
            return True
        if st == "open":
            return False
        if self._half_open_granted_at is not None and \
                time.monotonic() - self._half_open_granted_at < self.request_timeout_seconds:
            return False
        self._half_open_granted_at = time.monotonic()
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.last_reason = None
        self._half_open_granted_at = None

    def record_failure(self, reason: str | None = None) -> None:
        self.consecutive_failures += 1
        self.last_reason = reason
        self._half_open_granted_at = None
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


@dataclass(slots=True)
class ProviderModels:
    """Ответ на «какие модели доступны» — вместе с причиной, если их нет.

    Пустой список сам по себе не отличает «ключа нет» от «провайдер отказал» и
    от «моделей правда ноль». Владелец, который вставил ключ и увидел пустоту,
    ищет проблему вслепую, поэтому reason здесь обязателен для любого исхода,
    кроме ok."""
    status: str = "ok"                 # ok | unavailable | error
    models: list[str] = field(default_factory=list)
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _explain_status(name: str, status: int) -> str:
    """Отказ провайдера человеческим языком: владельцу нужно РАЗНОЕ действие."""
    if status == 401:
        return f"{name}: ключ отклонён (401) — замените ключ"
    if status == 402:
        return f"{name}: не хватает средств на счёте (402)"
    if status == 403:
        return f"{name}: ключ не допущен к этому запросу (403)"
    if status == 404:
        return f"{name}: адрес не найден (404) — проверьте base_url"
    if status == 429:
        return f"{name}: ограничение частоты запросов (429)"
    return f"{name}: HTTP {status}"


@dataclass(slots=True)
class HealthState:
    healthy: bool = True
    checked_at: float = 0.0
    error: str | None = None
    latency_ms: float | None = None


class OpenAIBackend:
    def __init__(self, config: BackendConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.semaphore = asyncio.Semaphore(max(1, config.max_concurrency))
        self.client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_seconds),
            transport=transport,
        )
        self.health = HealthState()
        self.breaker = CircuitBreaker(
            config.circuit_failure_threshold,
            config.circuit_cooldown_seconds,
            request_timeout_seconds=config.timeout_seconds,
        )

    def headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", **self.config.extra_headers}
        key = self.config.resolved_api_key()
        if key:
            headers["authorization"] = f"Bearer {key}"
        return headers

    async def close(self) -> None:
        await self.client.aclose()

    def resolve_path(self, path: str) -> str:
        """Путь запроса под конкретного провайдера.

        Gateway говорит на диалекте OpenAI («/v1/chat/completions»), но версия
        API у части провайдеров уже входит в base_url. Подмена делается здесь,
        а не в вызывающем коде: маршрут не обязан знать, как у бэкенда устроен
        префикс."""
        prefix = (self.config.api_path_prefix or "").rstrip("/")
        if prefix == "/v1" or not path.startswith("/v1"):
            return path
        return prefix + path[len("/v1"):]

    def unavailable_reason(self) -> str | None:
        """Почему провайдером нельзя пользоваться прямо сейчас (без сети).

        Отсутствующий ключ — не авария и не пустой список: провайдер просто
        недоступен, и владельцу нужно назвать переменную, которую он не задал.
        """
        if self.config.api_key_env and not self.config.resolved_api_key():
            return f"{self.config.api_key_env} не задан"
        return None

    async def list_models(self) -> ProviderModels:
        """Каталог провайдера: GET <models_path>. Любой отказ — с причиной.

        Ни один исход не роняет вызывающего: нет ключа → unavailable, отказ или
        обрыв → error с текстом. Ключ в текст не попадает никогда.
        """
        reason = self.unavailable_reason()
        if reason:
            return ProviderModels("unavailable", [], reason)
        path = self.resolve_path("/v1/models")
        # Каталог у облака бывает в мегабайт: короткий health-таймаут ему мал, а
        # таймаут инференса (минуты) — слишком велик для списка моделей.
        timeout = min(max(self.config.health_timeout_seconds, 15.0), self.config.timeout_seconds)
        try:
            r = await self.client.get(path, headers=self.headers(),
                                      timeout=httpx.Timeout(timeout))
        except httpx.HTTPError as exc:
            return ProviderModels("error", [], f"нет связи с {self.config.name}: {type(exc).__name__}")
        if r.status_code >= 400:
            return ProviderModels("error", [], _explain_status(self.config.name, r.status_code),
                                  {"status_code": r.status_code})
        try:
            data = r.json()
        except ValueError:
            return ProviderModels("error", [], f"{self.config.name} вернул не JSON")
        rows = data.get("data") if isinstance(data, dict) else data
        models = [str(m.get("id")) for m in (rows or []) if isinstance(m, dict) and m.get("id")]
        return ProviderModels("ok", models, None, {"count": len(models)})

    async def probe(self) -> HealthState:
        """Классификация пробы: здоров только 2xx. 401/403 — битые
        креды/конфиг, 429 — перегрузка, остальное и транспорт — больной
        бэкенд. Никакой «меньше 500 = здоров»: мониторинг не должен быть
        зелёным при 100% неработающих запросах. Probe живёт на коротком
        собственном таймауте, а не на таймауте инференса."""
        started = time.perf_counter()
        try:
            r = await self.client.get(
                self.config.health_path,
                headers=self.headers(),
                timeout=httpx.Timeout(self.config.health_timeout_seconds),
            )
            status = r.status_code
            if 200 <= status < 300:
                ok, error = True, None
            elif status in (401, 403):
                ok, error = False, f"HTTP {status}: credentials/config failure"
            elif status == 429:
                ok, error = False, f"HTTP {status}: throttled/unavailable"
            elif status >= 500:
                ok, error = False, f"HTTP {status}: backend error"
            else:
                ok, error = False, f"HTTP {status}: unexpected client error"
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
        self.health = HealthState(ok, time.time(), error, round((time.perf_counter()-started)*1000, 2))
        return self.health

    async def json_request(self, path: str, payload: dict) -> tuple[dict, httpx.Headers]:
        path = self.resolve_path(path)
        try:
            r = await self.client.post(path, json=payload, headers=self.headers())
        except httpx.TimeoutException as exc:
            self.breaker.record_failure(type(exc).__name__)
            raise BackendError(f"{self.config.name} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            self.breaker.record_failure(type(exc).__name__)
            raise BackendError(f"{self.config.name} transport error: {exc}") from exc
        if r.status_code >= 400:
            err = BackendError(f"{self.config.name} returned HTTP {r.status_code}: {r.text[:1000]}",
                               status_code=r.status_code)
            if err.failover:
                # только 5xx/429/408/425 двигают автомат; обычные клиентские
                # 4xx — ошибка запроса, бэкенд отвечает штатно
                self.breaker.record_failure(f"HTTP {r.status_code}")
            raise err
        try:
            body = r.json()
        except ValueError as exc:
            # битый JSON = нездоровый бэкенд → failover (status_code=None)
            self.breaker.record_failure("invalid JSON")
            raise BackendError(f"{self.config.name} returned invalid JSON", response_started=True) from exc
        try:
            body = self.normalize_response(path, body)
        except anthropic_protocol.ProtocolError as exc:
            self.breaker.record_failure("invalid provider response")
            raise BackendError(f"{self.config.name}: {exc}", response_started=True) from exc
        self.breaker.record_success()
        return body, r.headers

    def normalize_response(self, path: str, body: dict) -> dict:
        return body

    def stream_eof(self) -> None:
        self.breaker.record_success()

    async def stream_request(self, path: str, payload: dict) -> AsyncIterator[bytes]:
        path = self.resolve_path(path)
        deadline = asyncio.get_running_loop().time() + self.config.timeout_seconds
        response = None
        try:
            request = self.client.build_request("POST", path, json=payload, headers=self.headers())
            # Do not hold an asyncio.timeout context across a yield: its timer
            # would cancel the consumer while it works on an already-read chunk.
            response = await asyncio.wait_for(self.client.send(request, stream=True),
                                              timeout=self.config.timeout_seconds)
            raw = response.aiter_raw().__aiter__()
            total, error_body = 0, b""
            preloaded_error = response.status_code >= 400 and response.is_stream_consumed
            if preloaded_error:
                error_body = response.content[:1000]
            while True:
                if preloaded_error:
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("total stream deadline")
                try:
                    chunk = await asyncio.wait_for(anext(raw), timeout=remaining)
                except StopAsyncIteration:
                    break
                if response.status_code >= 400:
                    error_body += chunk[:1000 - len(error_body)]
                    if len(error_body) >= 1000:
                        break
                    continue
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    self.breaker.record_failure("stream size limit")
                    raise BackendError(f"{self.config.name} stream exceeds size limit", response_started=True)
                if chunk:
                    yield chunk
            if response.status_code >= 400:
                err = BackendError(
                    f"{self.config.name} returned HTTP {response.status_code}: "
                    f"{error_body.decode(errors='replace')}", status_code=response.status_code)
                if err.failover:
                    self.breaker.record_failure(f"HTTP {response.status_code}")
                raise err
        except (httpx.TimeoutException, TimeoutError) as exc:
            self.breaker.record_failure(type(exc).__name__)
            raise BackendError(f"{self.config.name} timed out: {exc}",
                               response_started=response is not None and response.status_code < 400) from exc
        except httpx.HTTPError as exc:
            self.breaker.record_failure(type(exc).__name__)
            raise BackendError(f"{self.config.name} transport error: {exc}",
                               response_started=response is not None and response.status_code < 400) from exc
        else:
            self.stream_eof()
        finally:
            if response is not None:
                await response.aclose()


class OpenRouterBackend(OpenAIBackend):
    """OpenRouter поверх общего OpenAI-бэкенда.

    Отдельный класс, а не копия: транспорт, автомат и health у OpenRouter ровно
    те же. Своё здесь только одно — сборка конфигурации из окружения владельца
    (ключ, адрес, заголовки атрибуции), чтобы «дал ключ → провайдер появился»
    не требовало правки yaml.
    """

    @classmethod
    def from_env(cls, transport: httpx.AsyncBaseTransport | None = None,
                 **overrides) -> "OpenRouterBackend":
        return cls(openrouter_backend_config(**overrides), transport)


class ZaiBackend(OpenAIBackend):
    """Z.ai напрямую (GLM). Версия API входит в base_url, поэтому пути к
    инференсу строятся без `/v1` — этим и отличается от OpenRouter."""

    @classmethod
    def from_env(cls, transport: httpx.AsyncBaseTransport | None = None,
                 **overrides) -> "ZaiBackend":
        return cls(zai_backend_config(**overrides), transport)


class AnthropicBackend(OpenAIBackend):
    """Anthropic — единственный провайдер набора не на диалекте OpenAI.

    Наследование здесь не ради экономии строк. Транспорт, семафор, автомат
    защиты, классификация отказов и health обязаны быть теми же самыми: девять
    провайдеров с девятью копиями этой машинерии — это девять мест, где однажды
    забудут про автомат, и заметит это владелец, а не тест.

    Своего у Anthropic ровно три вещи, и все три — форма запроса: ключ идёт в
    `x-api-key`, а не в `Authorization`; инференс живёт на `/v1/messages`;
    системное сообщение — отдельное поле, а не роль в списке.

    Каталог моделей читается у провайдера по-настоящему. В main он был зашит
    списком из трёх строк — то есть «доступные модели» показывались и там, где
    ключа нет и связи нет. Это не каталог, это надпись.
    """

    def headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json",
                   "anthropic-version": ANTHROPIC_VERSION,
                   **self.config.extra_headers}
        key = self.config.resolved_api_key()
        if key:
            # Anthropic не принимает Bearer: ключ идёт своим заголовком.
            headers["x-api-key"] = key
        return headers

    @staticmethod
    def to_anthropic_payload(payload: dict) -> dict:
        """Запрос в диалекте OpenAI → запрос Anthropic.

        `max_tokens` у Anthropic обязателен. Значение по умолчанию берётся
        явно и называется, а не подставляется молча где-то в транспорте.
        """
        return anthropic_protocol.request(payload, ANTHROPIC_DEFAULT_MAX_TOKENS)

    @staticmethod
    def to_openai_response(data: dict) -> dict:
        """Ответ Anthropic → форма, на которой говорит остальной шлюз."""
        return anthropic_protocol.response(data)

    def resolve_path(self, path: str) -> str:
        """`/v1/chat/completions` у Anthropic называется `/v1/messages`."""
        if path.endswith("/chat/completions"):
            return "/v1/messages"
        return super().resolve_path(path)

    async def json_request(self, path: str, payload: dict) -> tuple[dict, httpx.Headers]:
        if path.endswith("/chat/completions"):
            try:
                payload = self.to_anthropic_payload(payload)
            except anthropic_protocol.ProtocolError as exc:
                raise BackendError(str(exc), status_code=400) from exc
        return await super().json_request(path, payload)

    def normalize_response(self, path: str, body: dict) -> dict:
        # Validation must precede record_success; otherwise every malformed
        # native response resets the breaker just before recording its failure.
        return self.to_openai_response(body) if path == "/v1/messages" else body

    def stream_eof(self) -> None:
        # Native EOF proves nothing. Only the translator's message_stop can
        # close the breaker and issue an OpenAI finish event.
        return None

    async def stream_request(self, path: str, payload: dict) -> AsyncIterator[bytes]:
        if not path.endswith("/chat/completions"):
            async with aclosing(super().stream_request(path, payload)) as upstream:
                async for chunk in upstream:
                    yield chunk
            return
        try:
            payload = self.to_anthropic_payload(payload)
        except anthropic_protocol.ProtocolError as exc:
            raise BackendError(str(exc), status_code=400) from exc
        parser = anthropic_protocol.Stream()
        try:
            async with aclosing(super().stream_request(path, payload)) as upstream:
                async for chunk in upstream:
                    for normalized in parser.feed(chunk):
                        if parser.done:
                            # Close immediately, including when the provider
                            # keeps writing after message_stop or the consumer
                            # stops reading as soon as it sees the finish frame.
                            await upstream.aclose()
                            self.breaker.record_success()
                        yield normalized
                    if parser.done:
                        return
                parser.finish()
        except anthropic_protocol.ProtocolError as exc:
            self.breaker.record_failure("invalid Anthropic stream")
            raise BackendError(f"{self.config.name}: {exc}", response_started=True) from exc

    @classmethod
    def from_env(cls, transport: httpx.AsyncBaseTransport | None = None,
                 **overrides) -> "AnthropicBackend":
        return cls(anthropic_backend_config(**overrides), transport)


def build_backend(config: BackendConfig,
                  transport: httpx.AsyncBaseTransport | None = None) -> OpenAIBackend:
    """Бэкенд по его конфигурации. Неизвестный вид — обычный OpenAI-совместимый."""
    if config.kind == "openrouter" or config.name == "openrouter":
        return OpenRouterBackend(config, transport)
    if config.kind == "zai" or config.name == "zai":
        return ZaiBackend(config, transport)
    if config.kind == "anthropic" or config.name == "anthropic":
        return AnthropicBackend(config, transport)
    return OpenAIBackend(config, transport)
