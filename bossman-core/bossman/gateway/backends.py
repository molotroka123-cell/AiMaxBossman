from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from .config import BackendConfig


class BackendError(RuntimeError):
    pass


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
            raise BackendError(f"{self.config.name} returned HTTP {r.status_code}: {r.text[:1000]}")
        try:
            return r.json(), r.headers
        except ValueError as exc:
            raise BackendError(f"{self.config.name} returned invalid JSON") from exc

    async def stream_request(self, path: str, payload: dict) -> AsyncIterator[bytes]:
        async with self.client.stream("POST", path, json=payload, headers=self.headers()) as r:
            if r.status_code >= 400:
                body = (await r.aread())[:1000]
                raise BackendError(f"{self.config.name} returned HTTP {r.status_code}: {body.decode(errors='replace')}")
            async for chunk in r.aiter_raw():
                if chunk:
                    yield chunk
