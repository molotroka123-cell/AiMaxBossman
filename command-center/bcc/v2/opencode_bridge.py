"""Клиент HTTP-API `opencode serve` — v1 и v2.

Два поколения API, и они НЕ совместимы:

* v1 (OpenCode 0.x/1.x): корневые пути `/session`, `/session/{id}/message`,
  `/session/status`, `/session/{id}/abort`…; контракт сверен с
  `packages/sdk/openapi.json` исходников (см. docs/v2_1_agent_notes/lane-f-opencode.md).
* v2 (OpenCode 2.x, проверено на установленном v2.0.12): всё под `/api/…`,
  ответы в конверте `{"data": …}`, задание — `POST /api/session/{id}/prompt`
  (асинхронно, через inbox), остановка — `/interrupt`, занятые сессии —
  `GET /api/session/active`, сообщения — `{type: user|assistant, content: […]}`.
  Контракт снят с `GET /openapi.json` живого `opencode serve` v2.0.12.

R12 (2026-09-22): у v2 корневые v1-пути (`/session`, `/config`, `/project`)
отдают HTML веб-приложения с HTTP 200. Прежний health считал «жив» любой ответ
< 500 на `/config` и рапортовал online, хотя каждый вызов сессии падал. Теперь
health принимает ТОЛЬКО JSON ожидаемой формы (content-type + схема), определяет
поколение API и либо работает с ним, либо честно говорит `incompatible_version`.

Basic-auth: `OPENCODE_SERVER_USERNAME`/`OPENCODE_SERVER_PASSWORD` (v2 без
заданного пароля генерирует случайный и печатает его при старте — тогда health
честно ответит `unauthorized`).

BOSSMAN остаётся каноникой: миссии, задачи, бюджеты, права и история — у нас;
OpenCode — только исполнитель кодинг-сессии. Идентификатор сессии OpenCode
хранится в `opencode_sessions` и связан с task_id/run_id.

Правило доступа: `directory` — это ОДИН одобренный путь проекта/worktree,
а не «весь компьютер». Проверку пути делает вызывающий слой (инструмент/HTTP),
здесь она не дублируется, но и не обходится: без `directory` сервер работает
в своём cwd, поэтому мы всегда передаём его явно (v1 — query-параметром,
v2 — `location.directory` при создании сессии).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..providers import ProviderError, http_client

# Пробы поколения API. Каждая обязана вернуть JSON ожидаемой формы — одного
# «HTTP < 500» мало: у v2 корневые пути отдают HTML SPA с кодом 200.
V2_INFO = "/api/info"
V2_SESSIONS = "/api/session"
V1_SESSIONS = "/session"
V1_HEALTH = "/global/health"
# Совместимость: имя сохранено для внешних импортов; порядок проб теперь в detect().
HEALTH_PATHS = (V2_INFO, V2_SESSIONS, V1_SESSIONS)
HINT_START = ("запустите `opencode serve` на этой машине "
              "(см. docs/v2-pack/MCP_SKILLS_OPENCODE.md)")


class OpenCodeUnavailable(RuntimeError):
    """`opencode serve` не отвечает. Это состояние среды, а не баг интеграции."""


class OpenCodeIncompatible(httpx.HTTPError):
    """Сервер отвечает, но не тем API, который мы умеем (или не JSON).

    Наследник httpx.HTTPError: вызывающие слои уже ловят его и честно говорят
    «OpenCode недоступен (…)», а не падают пятисоткой на разборе HTML."""


def _is_json(r: httpx.Response) -> bool:
    ctype = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return ctype == "application/json" or ctype.endswith("+json")


def _json(r: httpx.Response, where: str) -> Any:
    """JSON ответа или OpenCodeIncompatible — никогда не JSONDecodeError наружу."""
    r.raise_for_status()
    if not _is_json(r):
        raise OpenCodeIncompatible(
            f"{where}: ожидался JSON, пришло {r.headers.get('content-type') or 'без типа'} "
            f"(HTTP {r.status_code}) — это не тот API OpenCode")
    try:
        return r.json()
    except ValueError as exc:
        raise OpenCodeIncompatible(f"{where}: тело не разбирается как JSON: {exc}") from exc


def _data(r: httpx.Response, where: str, kind: type) -> Any:
    """v2-конверт `{"data": …}` с проверкой типа содержимого."""
    body = _json(r, where)
    if not isinstance(body, dict) or not isinstance(body.get("data"), kind):
        raise OpenCodeIncompatible(f"{where}: нет поля data типа {kind.__name__} — не v2 API")
    return body["data"]


def _looks_like_sessions_v2(body: Any) -> bool:
    return (isinstance(body, dict) and isinstance(body.get("data"), list)
            and all(isinstance(s, dict) and str(s.get("id") or "").startswith("ses")
                    for s in body["data"]))


def _looks_like_sessions_v1(body: Any) -> bool:
    return (isinstance(body, list)
            and all(isinstance(s, dict) and isinstance(s.get("id"), str) for s in body))


def _v2_model(model: dict | None) -> dict | None:
    """v1 {providerID, modelID} → v2 Model.Ref {providerID, id}."""
    if not model:
        return None
    out = {"id": str(model.get("id") or model.get("modelID") or ""),
           "providerID": str(model.get("providerID") or "")}
    if model.get("variant"):
        out["variant"] = str(model["variant"])
    if not out["id"] or not out["providerID"]:
        raise OpenCodeIncompatible("model для v2 требует providerID и id (modelID)")
    return out


def _v2_message(msg: dict) -> dict:
    """v2 Session.Message.* → v1-форма {info:{id, role}, parts:[…]} для вызывающих."""
    kind = str(msg.get("type") or "")
    info = {"id": str(msg.get("id") or ""), "role": kind,
            "time": msg.get("time") or {}}
    if kind == "user":
        parts = [{"type": "text", "text": str(msg.get("text") or "")}]
    elif kind == "assistant":
        info.update(finish=msg.get("finish"), agent=msg.get("agent"),
                    model=msg.get("model"))
        parts = [dict(p) for p in msg.get("content") or [] if isinstance(p, dict)]
    else:
        parts = []
    return {"info": info, "parts": parts}


@dataclass(slots=True)
class OpenCodeBridge:
    """Тонкий клиент `opencode serve` (v1 или v2 — определяется сам).

    Все методы принимают `directory` — абсолютный путь одобренного проекта или
    worktree.
    """
    base_url: str = "http://127.0.0.1:4096"
    username: str = "opencode"
    password: str | None = None
    transport: Any = None
    directory: str = ""            # дефолтный проект, если вызов не задал свой
    api: str = field(default="", repr=False)   # "v1" | "v2" после detect(); "" — не определено
    poll_s: float = field(default=0.5, repr=False)

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        """То же правило про прокси, что и у провайдеров, — из одного места.

        `opencode serve` слушает на этой же машине, а httpx по умолчанию читает
        ALL_PROXY/HTTP_PROXY из окружения: запрос к 127.0.0.1:4096 уходил на
        прокси, который про этот адрес ничего не знает. При socks5-прокси без
        socksio до этого даже не доходило — клиент падал ImportError'ом прямо в
        конструкторе, и health(), обязанный отвечать «unavailable» вместо
        исключения, отдавал пятисотку.
        """
        auth = (self.username, self.password) if self.password else None
        return http_client(self.base_url, timeout=timeout, transport=self.transport,
                           base_url=self.base_url.rstrip("/"), auth=auth)

    def _params(self, directory: str = "", **extra) -> dict | None:
        params = {k: v for k, v in extra.items() if v is not None}
        d = directory or self.directory
        if d:
            params["directory"] = d
        return params or None

    # ------------------------------------------------------------- health

    async def detect(self, timeout: float = 5.0) -> dict:
        """Какое поколение API у сервера. Без исключений.

        status: online (api=v1|v2) | unauthorized | incompatible_version | unavailable.
        «online» — ТОЛЬКО по JSON ожидаемой формы с эндпоинта сессий, которым мы
        потом реально пользуемся. HTML с кодом 200 — это не «жив»."""
        seen: list[str] = []
        reached = unauthorized = False
        errors: list[str] = []

        async def get(path: str, **params):
            nonlocal reached, unauthorized
            try:
                async with self._client(timeout) as c:
                    r = await c.get(path, params=params or None)
            except (httpx.HTTPError, OSError, ProviderError) as exc:
                errors.append(f"{path}: {type(exc).__name__}")
                return None
            if r.status_code < 500:
                reached = True
            if r.status_code == 401:
                unauthorized = True
            ctype = (r.headers.get("content-type") or "?").split(";", 1)[0]
            seen.append(f"{path} → HTTP {r.status_code} {ctype}")
            if r.status_code != 200 or not _is_json(r):
                return None
            try:
                return r.json()
            except ValueError:
                return None

        base = {"base_url": self.base_url}
        # v2: /api/info (версия) + /api/session (форма конверта)
        info = await get(V2_INFO)
        version = None
        if isinstance(info, dict) and isinstance(info.get("version"), str):
            version = info["version"]
        sessions = await get(V2_SESSIONS, limit="1")
        if _looks_like_sessions_v2(sessions):
            self.api = "v2"
            return {"status": "online", "api": "v2", "version": version, "http": 200,
                    "probe": V2_SESSIONS, "capabilities": {"todo": False}, **base}
        # v1: /session — JSON-массив сессий
        sessions = await get(V1_SESSIONS)
        if _looks_like_sessions_v1(sessions):
            self.api = "v1"
            health = await get(V1_HEALTH)
            v1_version = health.get("version") if isinstance(health, dict) else None
            return {"status": "online", "api": "v1", "http": 200, "probe": V1_SESSIONS,
                    "version": v1_version if isinstance(v1_version, str) else None,
                    "capabilities": {"todo": True}, **base}
        self.api = ""
        if unauthorized:
            return {"status": "unauthorized", "detail": "; ".join(seen),
                    "hint": "задайте OPENCODE_PASSWORD (пароль `opencode serve`; v2 без "
                            "OPENCODE_SERVER_PASSWORD печатает случайный пароль при старте)",
                    **base}
        if reached:
            return {"status": "incompatible_version", "version": version,
                    "detail": "сервер отвечает, но ни v1 (/session → JSON-массив), ни v2 "
                              "(/api/session → {data: […]}) API сессий не найден: "
                              + "; ".join(seen),
                    "hint": "это не поддержанная версия OpenCode (или не OpenCode вовсе): "
                            "проверьте `opencode --version` и OPENCODE_URL",
                    **base}
        return {"status": "unavailable", "detail": "; ".join(seen + errors) or "нет ответа",
                "hint": HINT_START, **base}

    async def health(self, timeout: float = 5.0) -> dict:
        """Честный ответ о доступности, без исключений (см. detect)."""
        return await self.detect(timeout)

    async def _api(self) -> str:
        """Поколение API; не определилось — исключение с честной причиной."""
        if self.api:
            return self.api
        probe = await self.detect()
        if probe["status"] == "online":
            return self.api
        if probe["status"] == "unavailable":
            raise httpx.ConnectError(f"opencode serve не отвечает: {probe.get('detail')}")
        raise OpenCodeIncompatible(f"{probe['status']}: {probe.get('detail')}")

    # ------------------------------------------------------------- сессии

    async def create_session(self, directory: str = "", *, title: str = "",
                             agent: str = "", parent_id: str = "",
                             model: dict | None = None) -> dict:
        """Новая сессия в границах одобренного `directory`."""
        if await self._api() == "v2":
            if parent_id:
                raise OpenCodeIncompatible("v2 не создаёт дочернюю сессию по parentID — "
                                           "используйте fork")
            body: dict[str, Any] = {}
            if title:
                body["title"] = title
            if agent:
                body["agent"] = agent
            if model:
                body["model"] = _v2_model(model)
            d = directory or self.directory
            if d:
                body["location"] = {"directory": d}
            async with self._client() as c:
                r = await c.post(V2_SESSIONS, json=body)
            return _data(r, "POST /api/session", dict)
        body = {}
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
        return _json(r, "POST /session")

    async def get_session(self, session_id: str, directory: str = "") -> dict:
        if await self._api() == "v2":
            async with self._client() as c:
                r = await c.get(f"/api/session/{session_id}")
            return _data(r, "GET /api/session/{id}", dict)
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}", params=self._params(directory))
        return _json(r, "GET /session/{id}")

    async def list_sessions(self, directory: str = "") -> list[dict]:
        if await self._api() == "v2":
            async with self._client() as c:
                r = await c.get(V2_SESSIONS, params=self._params(directory))
            return _data(r, "GET /api/session", list)
        async with self._client() as c:
            r = await c.get("/session", params=self._params(directory))
        data = _json(r, "GET /session")
        return data if isinstance(data, list) else []

    async def status(self, directory: str = "") -> dict:
        """{sessionID: {"type": idle|busy|retry, ...}} — в v1-терминах для обоих API.

        v2 знает только занятые сессии (`/api/session/active`: running) — они
        отдаются как busy; отсутствие в карте — idle, как и в v1."""
        if await self._api() == "v2":
            async with self._client() as c:
                r = await c.get("/api/session/active")
            active = _data(r, "GET /api/session/active", dict)
            return {sid: {"type": "busy", "v2": v} for sid, v in active.items()}
        async with self._client() as c:
            r = await c.get("/session/status", params=self._params(directory))
        data = _json(r, "GET /session/status")
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

    async def _v2_prepare(self, c: httpx.AsyncClient, session_id: str, agent: str,
                          model: dict | None) -> None:
        """v2: агент и модель — свойства сессии, а не поля задания."""
        if agent:
            r = await c.post(f"/api/session/{session_id}/agent", json={"agent": agent})
            r.raise_for_status()
        if model:
            r = await c.post(f"/api/session/{session_id}/model",
                             json={"model": _v2_model(model)})
            r.raise_for_status()

    async def _v2_prompt(self, session_id: str, text: str, agent: str,
                         model: dict | None) -> dict:
        async with self._client(30) as c:
            await self._v2_prepare(c, session_id, agent, model)
            r = await c.post(f"/api/session/{session_id}/prompt", json={"text": text})
        return _data(r, "POST /api/session/{id}/prompt", dict)

    async def _v2_last_assistant(self, session_id: str) -> dict | None:
        async with self._client() as c:
            r = await c.get(f"/api/session/{session_id}/message",
                            params={"type": "assistant", "order": "desc", "limit": "1"})
        items = _data(r, "GET /api/session/{id}/message", list)
        return items[0] if items and isinstance(items[0], dict) else None

    async def _v2_wait_idle(self, session_id: str, since: float, timeout: float) -> None:
        """Ждём конца прогона. Сначала штатный wait; нет его — опрос active+сообщений."""
        try:
            async with self._client(timeout) as c:
                r = await c.post(f"/api/experimental/session/{session_id}/wait")
            if r.status_code in (200, 204):
                return
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            active = (await self.status()).get(session_id)
            if not active:
                last = await self._v2_last_assistant(session_id)
                created = float(((last or {}).get("time") or {}).get("created") or 0)
                completed = ((last or {}).get("time") or {}).get("completed")
                if last and completed and created >= since:
                    return
            await asyncio.sleep(self.poll_s)
        raise httpx.ReadTimeout(f"OpenCode не закончил шаг за {timeout:.0f} с")

    async def send_message(self, session_id: str, text: str, directory: str = "", *,
                           agent: str = "", model: dict | None = None,
                           timeout: float = 600.0) -> dict:
        """Синхронно: задание → ждём ответ ассистента → {info, parts}."""
        if await self._api() == "v2":
            queued = await self._v2_prompt(session_id, text, agent, model)
            since = float(((queued.get("time") or {}).get("created")) or 0)
            await self._v2_wait_idle(session_id, since, timeout)
            last = await self._v2_last_assistant(session_id)
            if not last:
                raise OpenCodeIncompatible("OpenCode v2 закончил шаг без сообщения ассистента")
            return _v2_message(last)
        body: dict[str, Any] = {"parts": self._parts(text)}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        async with self._client(timeout) as c:
            r = await c.post(f"/session/{session_id}/message", json=body,
                             params=self._params(directory))
        data = _json(r, "POST /session/{id}/message")
        return data if isinstance(data, dict) else {}

    async def prompt_async(self, session_id: str, text: str, directory: str = "", *,
                           agent: str = "", model: dict | None = None) -> bool:
        """Задание отдано, ответа не ждём.

        Нужен для длинных прогонов: воркер BOSSMAN не должен висеть на HTTP.
        """
        if await self._api() == "v2":
            await self._v2_prompt(session_id, text, agent, model)
            return True
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
        """Сообщения в v1-форме {info, parts} для обоих API."""
        if await self._api() == "v2":
            params = {"order": "asc"}
            if limit is not None:
                params["limit"] = str(limit)
            async with self._client() as c:
                r = await c.get(f"/api/session/{session_id}/message", params=params)
            return [_v2_message(m) for m in _data(r, "GET /api/session/{id}/message", list)
                    if isinstance(m, dict)]
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/message",
                            params=self._params(directory, limit=limit))
        data = _json(r, "GET /session/{id}/message")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------- управление

    async def abort(self, session_id: str, directory: str = "") -> bool:
        if await self._api() == "v2":
            async with self._client() as c:
                r = await c.post(f"/api/session/{session_id}/interrupt")
            body = _json(r, "POST /api/session/{id}/interrupt")
            if not isinstance(body, dict) or not isinstance(body.get("interrupted"), bool):
                raise OpenCodeIncompatible("interrupt: нет поля interrupted")
            # interrupted=false у v2 значит «прерывать было нечего» — сессия и так
            # не работает; для вызывающего это «остановлено».
            return True
        async with self._client() as c:
            r = await c.post(f"/session/{session_id}/abort",
                             params=self._params(directory))
        return bool(_json(r, "POST /session/{id}/abort"))

    async def fork(self, session_id: str, message_id: str | None = None,
                   directory: str = "") -> dict:
        if await self._api() == "v2":
            body = {"before": message_id} if message_id else {}
            async with self._client() as c:
                r = await c.post(f"/api/session/{session_id}/fork", json=body)
            return _data(r, "POST /api/session/{id}/fork", dict)
        body = {"messageID": message_id} if message_id else {}
        async with self._client() as c:
            r = await c.post(f"/session/{session_id}/fork", json=body,
                             params=self._params(directory))
        return _json(r, "POST /session/{id}/fork")

    async def diff(self, session_id: str, message_id: str | None = None,
                   directory: str = "") -> list[dict]:
        """[{file, patch, additions, deletions, status}] — одна форма у v1 и v2."""
        if await self._api() == "v2":
            if message_id:
                raise OpenCodeIncompatible("v2 не отдаёт дифф отдельного сообщения "
                                           "(message_id) — запросите дифф всей сессии")
            async with self._client() as c:
                r = await c.get(f"/api/session/{session_id}/diff")
            return _data(r, "GET /api/session/{id}/diff", list)
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/diff",
                            params=self._params(directory, messageID=message_id))
        data = _json(r, "GET /session/{id}/diff")
        return data if isinstance(data, list) else []

    async def children(self, session_id: str, directory: str = "") -> list[dict]:
        if await self._api() == "v2":
            async with self._client() as c:
                r = await c.get(V2_SESSIONS, params={"parentID": session_id})
            return _data(r, "GET /api/session?parentID", list)
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/children",
                            params=self._params(directory))
        data = _json(r, "GET /session/{id}/children")
        return data if isinstance(data, list) else []

    async def todo(self, session_id: str, directory: str = "") -> list[dict]:
        """Список дел агента. В v2 такого эндпоинта нет — пустой список
        (health отдаёт capabilities.todo=false, чтобы это было видно)."""
        if await self._api() == "v2":
            return []
        async with self._client() as c:
            r = await c.get(f"/session/{session_id}/todo",
                            params=self._params(directory))
        data = _json(r, "GET /session/{id}/todo")
        return data if isinstance(data, list) else []

    async def projects(self) -> list[dict]:
        path = "/api/project" if await self._api() == "v2" else "/project"
        async with self._client() as c:
            r = await c.get(path)
        data = _json(r, f"GET {path}")
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
