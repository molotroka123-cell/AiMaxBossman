"""Клиент HTTP-API `opencode serve`.

Контракт эндпоинтов взят НЕ на глаз: он сверен с `packages/sdk/openapi.json`
из исходников OpenCode (вендорная копия, см. docs/v2_1_agent_notes/lane-f-opencode.md).
Оттуда же — Basic-auth (`OPENCODE_SERVER_USERNAME`/`OPENCODE_SERVER_PASSWORD`)
и query-параметр `directory`, которым сессия привязывается к конкретному
проекту/worktree.

BOSSMAN остаётся каноникой: миссии, задачи, бюджеты, права и история — у нас;
OpenCode — только исполнитель кодинг-сессии. Идентификатор сессии OpenCode
хранится в `opencode_sessions` и связан с task_id/run_id.

Правило доступа: `directory` — это ОДИН одобренный путь проекта/worktree,
а не «весь компьютер». Проверку пути делает вызывающий слой (инструмент/HTTP),
здесь она не дублируется, но и не обходится: без `directory` сервер работает
в своём cwd, поэтому мы всегда передаём его явно.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# Порядок проб для health: сначала v2-эндпоинт здоровья, затем то, что точно
# есть у более старых сборок. Первый ответ < 500 означает «сервер жив».
HEALTH_PATHS = ("/api/health", "/doc", "/config", "/project")


class OpenCodeUnavailable(RuntimeError):
    """`opencode serve` не отвечает. Это состояние среды, а не баг интеграции."""


@dataclass(slots=True)
class OpenCodeBridge:
    """Тонкий клиент `opencode serve`.

    Все методы принимают `directory` — абсолютный путь одобренного проекта или
    worktree. Он уходит query-параметром, как того требует API OpenCode.
    """
    base_url: str = "http://127.0.0.1:4096"
    username: str = "opencode"
    password: str | None = None
    transport: Any = None
    directory: str = ""            # дефолтный проект, если вызов не задал свой

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        auth = (self.username, self.password) if self.password else None
        return httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            timeout=timeout,
            auth=auth,
            transport=self.transport,
        )

    def _params(self, directory: str = "", **extra) -> dict | None:
        params = {k: v for k, v in extra.items() if v is not None}
        d = directory or self.directory
        if d:
            params["directory"] = d
        return params or None

    # ------------------------------------------------------------- health

    async def health(self, timeout: float = 5.0) -> dict:
        """Честный ответ о доступности: online / unavailable, без исключений.

        Не 500 и не выдумка: если сервер не поднят, возвращается `unavailable`
        с типом ошибки и подсказкой, как поднять.
        """
        last = ""
        for path in HEALTH_PATHS:
            try:
                async with self._client(timeout) as c:
                    r = await c.get(path)
            except (httpx.HTTPError, OSError) as exc:
                last = type(exc).__name__
                continue
            if r.status_code < 500:
                return {"status": "online", "base_url": self.base_url,
                        "http": r.status_code, "probe": path}
            last = f"HTTP {r.status_code}"
        return {"status": "unavailable", "base_url": self.base_url,
                "detail": last or "нет ответа",
                "hint": "запустите `opencode serve` на этой машине "
                        "(см. docs/v2-pack/MCP_SKILLS_OPENCODE.md)"}

    # ------------------------------------------------------------- сессии

    async def create_session(self, directory: str = "", *, title: str = "",
                             agent: str = "", parent_id: str = "",
                             model: dict | None = None) -> dict:
        """POST /session — новая сессия в границах одобренного `directory`."""
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if agent:
            body["agent"] = agent
        if parent_id:
            body["parentID"] = parent_id
        if model:
            body["model"] = model
        async with self._client() as c:
            r = await c.post("/session", json=body, params=self._params(directory))
        r.raise_for_status()
        return r.json()

    async def get_session(self, session_id: str, directory: str = "") -> dict:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}", params=self._params(directory))
        r.raise_for_status()
        return r.json()

    async def list_sessions(self, directory: str = "") -> list[dict]:
        async with self._client() as c:
            r = await c.get("/session", params=self._params(directory))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def status(self, directory: str = "") -> dict:
        """GET /session/status → {sessionID: {"type": idle|busy|retry, ...}}."""
        async with self._client() as c:
            r = await c.get("/session/status", params=self._params(directory))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    async def session_status(self, session_id: str, directory: str = "") -> dict:
        """Статус ОДНОЙ сессии. Нет в карте статусов → считаем idle."""
        table = await self.status(directory)
        value = table.get(session_id)
        if isinstance(value, dict):
            return value
        return {"type": "idle"}

    # ------------------------------------------------------------ задание

    @staticmethod
    def _parts(text: str) -> list[dict]:
        return [{"type": "text", "text": text}]

    async def send_message(self, session_id: str, text: str, directory: str = "", *,
                           agent: str = "", model: dict | None = None,
                           timeout: float = 600.0) -> dict:
        """POST /session/{id}/message — синхронно: ждём ответ ассистента."""
        body: dict[str, Any] = {"parts": self._parts(text)}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        async with self._client(timeout) as c:
            r = await c.post(f"/session/{session_id}/message", json=body,
                             params=self._params(directory))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    async def prompt_async(self, session_id: str, text: str, directory: str = "", *,
                           agent: str = "", model: dict | None = None) -> bool:
        """POST /session/{id}/prompt_async — задание отдано, ответа не ждём.

        Нужен для длинных прогонов: воркер BOSSMAN не должен висеть на HTTP.
        """
        body: dict[str, Any] = {"parts": self._parts(text)}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        async with self._client(30) as c:
            r = await c.post(f"/session/{session_id}/prompt_async", json=body,
                             params=self._params(directory))
        r.raise_for_status()
        return True

    async def messages(self, session_id: str, directory: str = "",
                       limit: int | None = None) -> list[dict]:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/message",
                            params=self._params(directory, limit=limit))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    # ------------------------------------------------------- управление

    async def abort(self, session_id: str, directory: str = "") -> bool:
        async with self._client() as c:
            r = await c.post(f"/session/{session_id}/abort",
                             params=self._params(directory))
        r.raise_for_status()
        return bool(r.json())

    async def fork(self, session_id: str, message_id: str | None = None,
                   directory: str = "") -> dict:
        body = {"messageID": message_id} if message_id else {}
        async with self._client() as c:
            r = await c.post(f"/session/{session_id}/fork", json=body,
                             params=self._params(directory))
        r.raise_for_status()
        return r.json()

    async def diff(self, session_id: str, message_id: str | None = None,
                   directory: str = "") -> list[dict]:
        """GET /session/{id}/diff → [{file, patch, additions, deletions, status}]."""
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/diff",
                            params=self._params(directory, messageID=message_id))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def children(self, session_id: str, directory: str = "") -> list[dict]:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/children",
                            params=self._params(directory))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def todo(self, session_id: str, directory: str = "") -> list[dict]:
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/todo",
                            params=self._params(directory))
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def projects(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/project")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


# --------------------------------------------------------------- утилиты

def diff_summary(diffs: list[dict]) -> dict:
    """Свод по списку SnapshotFileDiff — то, что реально нужно Governor'у."""
    files = [str(d.get("file") or "") for d in diffs if isinstance(d, dict)]
    return {
        "files": len([f for f in files if f]),
        "additions": sum(int(d.get("additions") or 0) for d in diffs if isinstance(d, dict)),
        "deletions": sum(int(d.get("deletions") or 0) for d in diffs if isinstance(d, dict)),
        "paths": [f for f in files if f],
    }


def render_diff(diffs: list[dict], limit: int = 8000) -> tuple[str, bool]:
    """Текст диффа для модели + флаг обрезки. Патчи режем, а не выдумываем."""
    chunks: list[str] = []
    for d in diffs:
        if not isinstance(d, dict):
            continue
        head = (f"--- {d.get('file') or '?'} "
                f"({d.get('status') or 'modified'}, "
                f"+{int(d.get('additions') or 0)}/-{int(d.get('deletions') or 0)})")
        chunks.append(head + "\n" + str(d.get("patch") or "").rstrip())
    text = "\n\n".join(chunks)
    if len(text) > limit:
        return text[:limit], True
    return text, False


def assistant_text(message: dict) -> str:
    """Текст ответа из {info, parts} — берём только текстовые части."""
    parts = message.get("parts") if isinstance(message, dict) else None
    if not isinstance(parts, list):
        return ""
    out = [str(p.get("text") or "") for p in parts
           if isinstance(p, dict) and p.get("type") == "text"]
    return "\n".join(t for t in out if t).strip()
