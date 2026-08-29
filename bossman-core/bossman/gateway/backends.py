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

    def headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", **self.config.extra_headers}
        key = self.config.resolved_api_key()
        if key:
            headers["authorization"] = f"Bearer {key}"
        return headers

    async def close(self) -> None:
        await self.client.aclose()

    async def probe(self) -> HealthState:
        started = time.perf_counter()
        try:
            r = await self.client.get(self.config.health_path, headers=self.headers())
            ok = r.status_code < 500
            error = None if ok else f"HTTP {r.status_code}"
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
        self.health = HealthState(ok, time.time(), error, round((time.perf_counter()-started)*1000, 2))
        return self.health

    async def json_request(self, path: str, payload: dict) -> tuple[dict, httpx.Headers]:
        r = await self.client.post(path, json=payload, headers=self.headers())
        if r.status_code >= 400:
            raise BackendError(f"{self.config.name} returned HTTP {r.status_code}: {r.text[:1000]}",
                               status_code=r.status_code)
        try:
            return r.json(), r.headers
        except ValueError as exc:
            # битый JSON = нездоровый бэкенд → failover (status_code=None)
            raise BackendError(f"{self.config.name} returned invalid JSON") from exc

    async def stream_request(self, path: str, payload: dict) -> AsyncIterator[bytes]:
        async with self.client.stream("POST", path, json=payload, headers=self.headers()) as r:
            if r.status_code >= 400:
                body = (await r.aread())[:1000]
                raise BackendError(f"{self.config.name} returned HTTP {r.status_code}: {body.decode(errors='replace')}",
                                   status_code=r.status_code)
            async for chunk in r.aiter_raw():
                if chunk:
                    yield chunk
