from __future__ import annotations

import asyncio
import os
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator, Callable

import httpx


class GatewayCloudDenied(RuntimeError):
    """Gateway отказал в облаке по политике (HTTP 403 POLICY_DENIED).

    Отдельный тип, чтобы ядро отличило запрет облака от обычной ошибки апстрима
    и превратило его в CloudDenied/NeedsCloudApproval по политике агента."""


# Ограниченный retry (429/503/сеть). chat/completions на уровне Gateway —
# без побочных эффектов: повтор идемпотентен. Эффекты инструментов (запись,
# exec, отправка) через этот путь НЕ проходят никогда — они живут в
# runner._call_tool с правами и подтверждениями, retry их не дублирует.
RETRY_STATUSES = frozenset({429, 503})
RETRY_MAX = 4            # бюджет повторов (всего попыток RETRY_MAX + 1)
RETRY_DEADLINE_S = 60.0  # общий дедлайн на все попытки, включая ожидания
RETRY_BASE_S = 0.5       # экспонента: base * 2**attempt, ± jitter
RETRY_CAP_S = 8.0        # потолок одной паузы (Retry-After может быть больше, но не дедлайна)


def _retry_after_seconds(value: str | None) -> float | None:
    """Retry-After: секунды или HTTP-дата. Непонятное значение — игнорируем."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time.time())


class GatewayClient:
    """Thin reusable client for Bossman Core and future local applications."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 600.0,
                 *, retry_max: int = RETRY_MAX, retry_deadline_s: float = RETRY_DEADLINE_S,
                 retry_base_s: float = RETRY_BASE_S):
        self.base_url = (base_url or os.getenv("BOSSMAN_GATEWAY_URL", "http://127.0.0.1:8765/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("BOSSMAN_GATEWAY_CORE_KEY", "")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self.retry_max = max(0, int(retry_max))
        self.retry_deadline_s = float(retry_deadline_s)
        self.retry_base_s = float(retry_base_s)
        # Инъекции для тестов (детерминированный backoff без реального сна).
        self._sleep: Callable[[float], Any] = asyncio.sleep
        self._rand: Callable[[], float] = random.random
        self._clock: Callable[[], float] = time.monotonic

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def close(self) -> None:
        await self._client.aclose()

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        """Экспонента с jitter; Retry-After сервера имеет приоритет (если задан)."""
        if retry_after is not None:
            return retry_after
        base = min(RETRY_CAP_S, self.retry_base_s * (2 ** attempt))
        return base * (0.5 + self._rand())  # jitter в [0.5x, 1.5x)

    async def _post_with_retry(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> httpx.Response:
        """POST с ограниченным повтором ТОЛЬКО на 429/503/ошибках соединения.

        Бюджет: retry_max повторов и общий дедлайн retry_deadline_s. Исчерпано →
        честный отказ: последняя ошибка (HTTPStatusError с ответом 429/503 или
        исключение транспорта) поднимается наверх, не «тихий None»."""
        started = self._clock()
        attempt = 0
        last_exc: Exception | None = None
        while True:
            try:
                r = await self._client.post(url, headers=headers, json=json)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError,
                    httpx.ReadTimeout, httpx.PoolTimeout) as exc:
                last_exc = exc
                retry_after = None
            else:
                if r.status_code not in RETRY_STATUSES:
                    return r
                last_exc = httpx.HTTPStatusError(
                    f"gateway returned {r.status_code} after {attempt} retries",
                    request=r.request, response=r)
                retry_after = _retry_after_seconds(r.headers.get("retry-after"))
            if attempt >= self.retry_max:
                raise last_exc
            delay = self._backoff(attempt, retry_after)
            elapsed = self._clock() - started
            if elapsed + delay > self.retry_deadline_s:
                # Дедлайн: ждать дольше нельзя — отдаём последнюю ошибку как есть.
                raise last_exc
            attempt += 1
            await self._sleep(delay)

    async def chat(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int | None = None, cloud_allowed: bool = False,
                   session_id: str = "", cache_ttl: str | None = None,
                   run_id: str | int | None = None, **extra: Any) -> dict:
        payload: dict[str, Any] = {"model": model, "messages": messages, **extra}
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = dict(self._headers())
        # Облачная политика агента едет заголовком, а не полем тела: в тело нельзя,
        # оно уходит апстриму. Gateway сам вырежет облачные цели при "0".
        # F-008: по умолчанию "0" (fail-closed) — "1" только если вызывающий
        # явно передал cloud_allowed=True по политике агента.
        headers["X-Bossman-Cloud-Allowed"] = "1" if cloud_allowed else "0"
        if session_id:
            headers["X-Bossman-Session-Id"] = session_id
        if cache_ttl:
            headers["X-Bossman-Cache-TTL"] = cache_ttl
        if run_id is not None:
            headers["X-Bossman-Run-Id"] = str(run_id)
        r = await self._post_with_retry(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if r.status_code == 403:
            try:
                code = (r.json().get("error") or {}).get("code")
            except Exception:
                code = None
            if code == "POLICY_DENIED":
                raise GatewayCloudDenied(model)
        r.raise_for_status()
        data = r.json()
        # F-008: облачность РАЗРЕШЁННОГО маршрута (заголовок Gateway) — для
        # аудита cloud_calls/model_calls в llm.py. Нет заголовка (старый Gateway,
        # чужой сервер) → None: вызывающий откатывается на is_cloud(alias).
        cloud_hdr = r.headers.get("x-bossman-cloud")
        if isinstance(data, dict) and cloud_hdr is not None:
            data["_bossman_cloud"] = cloud_hdr.strip() == "1"
        return data

    async def embeddings(self, *, model: str, input: str | list[str], cloud_allowed: bool = False,
                         **extra: Any) -> dict:
        headers = dict(self._headers())
        # F-008: эмбеддинги — тот же egress; по умолчанию облако закрыто.
        headers["X-Bossman-Cloud-Allowed"] = "1" if cloud_allowed else "0"
        r = await self._client.post(f"{self.base_url}/embeddings", headers=headers,
                                    json={"model": model, "input": input, **extra})
        r.raise_for_status()
        return r.json()

    async def metrics(self) -> dict:
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        r = await self._client.get(f"{root}/metrics", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def stream_chat(self, *, model: str, messages: list[dict], cloud_allowed: bool = False,
                          session_id: str = "", cache_ttl: str | None = None,
                          run_id: str | int | None = None, **extra: Any) -> AsyncIterator[bytes]:
        payload = {"model": model, "messages": messages, "stream": True, **extra}
        headers = dict(self._headers())
        headers["X-Bossman-Cloud-Allowed"] = "1" if cloud_allowed else "0"
        if session_id:
            headers["X-Bossman-Session-Id"] = session_id
        if cache_ttl:
            headers["X-Bossman-Cache-TTL"] = cache_ttl
        if run_id is not None:
            headers["X-Bossman-Run-Id"] = str(run_id)
        async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload) as r:
            r.raise_for_status()
            async for chunk in r.aiter_raw():
                if chunk:
                    yield chunk
