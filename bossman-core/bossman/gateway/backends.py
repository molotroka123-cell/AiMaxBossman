from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from .config import BackendConfig


# 4xx, при которых переключение на следующий таргет оправдано (бэкенд занят/
# таймаут), в отличие от 400/401/403/404/422 — ошибок самого запроса/политики,
# которые дал бы любой таргет.
_FAILOVER_4XX = {408, 425, 429}


class BackendError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

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
            raise BackendError(f"{self.config.name} returned invalid JSON") from exc
        self.breaker.record_success()
        return body, r.headers

    async def stream_request(self, path: str, payload: dict) -> AsyncIterator[bytes]:
        try:
            async with self.client.stream("POST", path, json=payload, headers=self.headers()) as r:
                if r.status_code >= 400:
                    body = (await r.aread())[:1000]
                    err = BackendError(f"{self.config.name} returned HTTP {r.status_code}: {body.decode(errors='replace')}",
                                       status_code=r.status_code)
                    if err.failover:
                        self.breaker.record_failure(f"HTTP {r.status_code}")
                    raise err
                async for chunk in r.aiter_raw():
                    if chunk:
                        yield chunk
        except httpx.TimeoutException as exc:
            self.breaker.record_failure(type(exc).__name__)
            raise BackendError(f"{self.config.name} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            self.breaker.record_failure(type(exc).__name__)
            raise BackendError(f"{self.config.name} transport error: {exc}") from exc
        else:
            self.breaker.record_success()
