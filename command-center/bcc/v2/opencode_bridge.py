from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import httpx

@dataclass(slots=True)
class OpenCodeBridge:
    """Thin client for `opencode serve`.

    Keep BOSSMAN mission/task state canonical. Persist OpenCode session IDs
    against BOSSMAN runs in the integration layer.
    """
    base_url: str = "http://127.0.0.1:4096"
    username: str = "opencode"
    password: str | None = None
    transport: Any = None

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        auth = (self.username, self.password) if self.password else None
        return httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            timeout=timeout,
            auth=auth,
            transport=self.transport,
        )

    async def get_session(self, session_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}")
        r.raise_for_status()
        return r.json()

    async def abort(self, session_id: str) -> bool:
        async with self._client() as c:
            r = await c.post(f"/session/{session_id}/abort")
        r.raise_for_status()
        return bool(r.json())

    async def fork(self, session_id: str, message_id: str | None = None) -> dict:
        body = {"messageID": message_id} if message_id else {}
        async with self._client() as c:
            r = await c.post(f"/session/{session_id}/fork", json=body)
        r.raise_for_status()
        return r.json()

    async def diff(self, session_id: str, message_id: str | None = None) -> list[dict]:
        params = {"messageID": message_id} if message_id else None
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/diff", params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def children(self, session_id: str) -> list[dict]:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/children")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def todo(self, session_id: str) -> list[dict]:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/todo")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
