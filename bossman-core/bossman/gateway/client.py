from __future__ import annotations

import os
from typing import Any, AsyncIterator

import httpx


class GatewayCloudDenied(RuntimeError):
    """Gateway отказал в облаке по политике (HTTP 403 POLICY_DENIED).

    Отдельный тип, чтобы ядро отличило запрет облака от обычной ошибки апстрима
    и превратило его в CloudDenied/NeedsCloudApproval по политике агента."""


class GatewayClient:
    """Thin reusable client for Bossman Core and future local applications."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 600.0):
        self.base_url = (base_url or os.getenv("BOSSMAN_GATEWAY_URL", "http://127.0.0.1:8765/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("BOSSMAN_GATEWAY_CORE_KEY", "")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
                   max_tokens: int | None = None, cloud_allowed: bool = True, **extra: Any) -> dict:
        payload: dict[str, Any] = {"model": model, "messages": messages, **extra}
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = dict(self._headers())
        # Облачная политика агента едет заголовком, а не полем тела: в тело нельзя,
        # оно уходит апстриму. Gateway сам вырежет облачные цели при "0".
        headers["X-Bossman-Cloud-Allowed"] = "1" if cloud_allowed else "0"
        r = await self._client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if r.status_code == 403:
            try:
                code = (r.json().get("error") or {}).get("code")
            except Exception:
                code = None
            if code == "POLICY_DENIED":
                raise GatewayCloudDenied(model)
        r.raise_for_status()
        return r.json()

    async def embeddings(self, *, model: str, input: str | list[str], **extra: Any) -> dict:
        r = await self._client.post(f"{self.base_url}/embeddings", headers=self._headers(), json={"model": model, "input": input, **extra})
        r.raise_for_status()
        return r.json()

    async def stream_chat(self, *, model: str, messages: list[dict], **extra: Any) -> AsyncIterator[bytes]:
        payload = {"model": model, "messages": messages, "stream": True, **extra}
        async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=payload) as r:
            r.raise_for_status()
            async for chunk in r.aiter_raw():
                if chunk:
                    yield chunk
