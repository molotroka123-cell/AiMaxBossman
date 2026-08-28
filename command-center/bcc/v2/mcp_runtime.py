"""MCP client runtime (V2.1, фаза D) — реальное исполнение протокола.

Протокол НЕ хэндроллится: используется официальный SDK `mcp` (2.x,
`mcp.client.client.Client` + `StdioServerParameters`). Импорт ленивый, так что
отсутствие пакета не ломает старт приложения (см. `bcc/api.py::_wire_v2_managers`).

Ключевое устройство: SDK построен на anyio-таскгруппах, а таскгруппу нельзя
входить в одной задаче и выходить в другой. Поэтому на каждый сервер заводится
ОДНА задача-водитель (`_Connection._run`), которая владеет контекстом `Client`,
а все вызовы приходят к ней через очередь и возвращаются через future.
Тогда `connect/call/disconnect` можно звать откуда угодно (HTTP-эндпоинт,
хэндлер инструмента, тик фичи) без нарушения контракта anyio.

Ошибка сервера — это ДАННЫЕ (`MCPCallError`), а не падение процесса.
Падение сервера переводит соединение в `unhealthy` и порождает событие.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .mcp_hub import MCPServerSpec, MCPToolView

# --------------------------------------------------------------- SDK (лениво)

SDK_HINT = "pip install mcp   (официальный Model Context Protocol SDK)"


def sdk_available() -> bool:
    try:
        load_sdk()
    except Exception:
        return False
    return True


def load_sdk() -> tuple[Any, Any]:
    """(Client, StdioServerParameters) из официального SDK. Бросает при отсутствии."""
    from mcp import StdioServerParameters                     # type: ignore
    from mcp.client.client import Client                      # type: ignore
    return Client, StdioServerParameters


def sdk_version() -> str:
    try:
        from importlib.metadata import version
        return version("mcp")
    except Exception:
        return ""


class MCPUnavailable(RuntimeError):
    """SDK не установлен или сервер не сконфигурирован — честный отказ, не 500."""


class MCPCallError(RuntimeError):
    """Ошибка вызова инструмента: возвращается модели как данные."""


# --------------------------------------------------------------- результаты

@dataclass(slots=True)
class MCPCallResult:
    text: str = ""
    is_error: bool = False
    structured: Any = None
    blocks: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class ServerHealth:
    server_id: str
    status: str = "unknown"        # unknown | connecting | healthy | unhealthy | stopped
    detail: str = ""
    tools: int = 0
    connected: bool = False

    def as_dict(self) -> dict:
        return {"server": self.server_id, "status": self.status, "detail": self.detail,
                "tools": self.tools, "connected": self.connected}


def _text_of(result: Any) -> tuple[str, bool, Any, list[dict]]:
    """CallToolResult SDK → плоский текст + структурная часть."""
    blocks: list[dict] = []
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "") or ""
        if kind == "text":
            text = getattr(block, "text", "") or ""
            parts.append(text)
            blocks.append({"type": "text", "text": text})
        elif kind in ("image", "audio"):
            mime = getattr(block, "mimeType", None) or getattr(block, "mime_type", "") or ""
            parts.append(f"[{kind} {mime}]")
            blocks.append({"type": kind, "mime": mime})
        else:
            parts.append(str(block))
            blocks.append({"type": kind or "unknown"})
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(str(structured))
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    return "\n".join(p for p in parts if p), is_error, structured, blocks


def _schema_of(tool: Any) -> dict:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return schema if isinstance(schema, dict) else {}


# --------------------------------------------------------------- соединение

_STOP = object()


class _Connection:
    """Одна задача владеет контекстом Client; наружу — очередь запросов."""

    def __init__(self, spec: MCPServerSpec, *,
                 on_event: Callable[[str, dict], Awaitable[None]] | None = None) -> None:
        self.spec = spec
        self.on_event = on_event
        self.health = ServerHealth(server_id=spec.id, status="unknown")
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._crashed_notified = False

    # ---- жизненный цикл

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self.health.status = "connecting"
        self._crashed_notified = False
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.spec.id}")
        try:
            await asyncio.wait_for(asyncio.shield(self._ready),
                                   timeout=max(5.0, float(self.spec.timeout_seconds)))
        except asyncio.TimeoutError:
            await self.stop()
            raise MCPUnavailable(f"MCP-сервер {self.spec.id}: не ответил на инициализацию")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self._queue.put_nowait(_STOP)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception:
            pass
        if self.health.status not in ("unhealthy",):
            self.health.status = "stopped"
        self.health.connected = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- запросы

    async def request(self, method: str, *args, timeout: float | None = None, **kwargs) -> Any:
        if not self.running:
            raise MCPUnavailable(f"MCP-сервер {self.spec.id} не подключён")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._queue.put_nowait((method, args, kwargs, fut))
        limit = timeout if timeout is not None else float(self.spec.timeout_seconds)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=limit)
        except asyncio.TimeoutError:
            fut.cancel()
            raise MCPCallError(
                f"MCP {self.spec.id}.{method}: таймаут {limit:.0f} с") from None

    # ---- внутренняя петля

    def _params(self, Params: Any) -> Any:
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "SYSTEMROOT", "PYTHONPATH")
               if k in os.environ}
        for key in self.spec.env_keys:
            if key in os.environ:
                env[key] = os.environ[key]
        command = list(self.spec.command)
        exe = command[0]
        if exe in ("python", "python3") and not shutil.which(exe):
            exe = sys.executable
        return Params(command=exe, args=command[1:], env=env, cwd=self.spec.cwd or None)

    async def _run(self) -> None:
        assert self._ready is not None
        try:
            Client, Params = load_sdk()
        except Exception as exc:
            self._fail_ready(MCPUnavailable(f"MCP SDK недоступен ({exc}); {SDK_HINT}"))
            self.health.status = "unhealthy"
            self.health.detail = f"SDK: {exc}"
            return

        if self.spec.transport != "stdio":
            self._fail_ready(MCPUnavailable(
                f"транспорт {self.spec.transport} пока не поддержан рантаймом (только stdio)"))
            self.health.status = "unhealthy"
            self.health.detail = "unsupported transport"
            return

        crash: BaseException | None = None
        try:
            async with Client(self._params(Params)) as client:
                self.health.status = "healthy"
                self.health.connected = True
                self.health.detail = ""
                if not self._ready.done():
                    self._ready.set_result(True)
                await self._pump(client)
        except asyncio.CancelledError:
            self.health.status = "stopped"
            self.health.connected = False
            raise
        except BaseException as exc:                     # ExceptionGroup из anyio тоже сюда
            crash = exc
            self.health.status = "unhealthy"
            self.health.connected = False
            self.health.detail = f"{type(exc).__name__}: {exc}"[:400]
            self._fail_ready(MCPUnavailable(
                f"MCP-сервер {self.spec.id} не поднялся: {type(exc).__name__}: {exc}"))
        finally:
            self.health.connected = False
            self._drain(crash)
            if crash is not None:
                await self._notify_crash(f"{type(crash).__name__}: {crash}"[:400])

    async def _pump(self, client: Any) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            method, args, kwargs, fut = item
            try:
                value = await getattr(client, method)(*args, **kwargs)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.set_exception(MCPCallError(f"MCP {self.spec.id}: вызов прерван"))
                raise
            except BaseException as exc:
                if not fut.done():
                    fut.set_exception(MCPCallError(
                        f"MCP {self.spec.id}.{method}: {type(exc).__name__}: {exc}"))
                if _is_transport_dead(exc):
                    self.health.status = "unhealthy"
                    self.health.connected = False
                    self.health.detail = f"{type(exc).__name__}: {exc}"[:400]
                    await self._notify_crash(self.health.detail)
                    return
            else:
                if not fut.done():
                    fut.set_result(value)

    def _drain(self, crash: BaseException | None) -> None:
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is _STOP:
                continue
            _m, _a, _k, fut = item
            if not fut.done():
                fut.set_exception(MCPCallError(
                    f"MCP-сервер {self.spec.id} отключился" + (f": {crash}" if crash else "")))

    def _fail_ready(self, exc: BaseException) -> None:
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(exc)

    async def _notify_crash(self, detail: str) -> None:
        if self._crashed_notified or self.on_event is None:
            return
        self._crashed_notified = True
        try:
            await self.on_event("mcp.unhealthy",
                                {"server": self.spec.id, "detail": detail,
                                 "status": "unhealthy"})
        except Exception:
            pass


def _is_transport_dead(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in
               ("connection closed", "brokenpipe", "broken pipe", "closedresource",
                "endofstream", "anyio.endofstream", "closed"))


# --------------------------------------------------------------- рантайм

class MCPRuntime:
    """Пул соединений по id сервера. Все методы безопасны при отсутствии SDK."""

    def __init__(self, *, on_event: Callable[[str, dict], Awaitable[None]] | None = None) -> None:
        self._conns: dict[str, _Connection] = {}
        self._specs: dict[str, MCPServerSpec] = {}
        self.on_event = on_event

    # ---- управление

    def spec_for(self, server_id: str) -> MCPServerSpec | None:
        return self._specs.get(str(server_id))

    def remember(self, spec: MCPServerSpec) -> None:
        self._specs[str(spec.id)] = spec

    async def connect(self, spec: MCPServerSpec) -> ServerHealth:
        errors = spec.validate()
        if errors:
            raise MCPUnavailable("; ".join(errors))
        if not sdk_available():
            raise MCPUnavailable(f"MCP SDK не установлен; {SDK_HINT}")
        self.remember(spec)
        conn = self._conns.get(str(spec.id))
        if conn is not None and conn.running and conn.spec != spec:
            await self.disconnect(spec.id)
            conn = None
        if conn is None or not conn.running:
            conn = _Connection(spec, on_event=self.on_event)
            self._conns[str(spec.id)] = conn
            await conn.start()
        return conn.health

    async def disconnect(self, server_id: str) -> None:
        conn = self._conns.pop(str(server_id), None)
        if conn is not None:
            await conn.stop()

    async def shutdown(self) -> None:
        for sid in list(self._conns):
            await self.disconnect(sid)

    def _conn(self, server_id: str) -> _Connection:
        conn = self._conns.get(str(server_id))
        if conn is None or not conn.running:
            raise MCPUnavailable(f"MCP-сервер {server_id} не подключён")
        return conn

    async def ensure(self, spec: MCPServerSpec) -> _Connection:
        """Подключиться, если ещё нет (ленивое соединение при вызове инструмента)."""
        conn = self._conns.get(str(spec.id))
        if conn is not None and conn.running:
            return conn
        await self.connect(spec)
        return self._conn(spec.id)

    # ---- наблюдаемость

    def health(self, server_id: str) -> ServerHealth:
        conn = self._conns.get(str(server_id))
        if conn is None:
            return ServerHealth(server_id=str(server_id), status="unknown",
                                detail="соединение не открывалось")
        return conn.health

    def statuses(self) -> list[dict]:
        return [c.health.as_dict() for c in self._conns.values()]

    async def probe(self, server_id: str) -> ServerHealth:
        """Живая проверка: реальный `tools/list` мимо кэша SDK."""
        conn = self._conns.get(str(server_id))
        if conn is None or not conn.running:
            return self.health(server_id)
        try:
            result = await conn.request("list_tools", cache_mode="refresh", timeout=10.0)
        except Exception as exc:
            conn.health.status = "unhealthy"
            conn.health.connected = False
            conn.health.detail = str(exc)[:400]
            await conn._notify_crash(conn.health.detail)
            return conn.health
        conn.health.status = "healthy"
        conn.health.connected = True
        conn.health.tools = len(getattr(result, "tools", []) or [])
        conn.health.detail = ""
        return conn.health

    # ---- протокол

    async def list_tools(self, server_id: str, *, refresh: bool = False) -> list[MCPToolView]:
        conn = self._conn(server_id)
        result = await conn.request("list_tools",
                                    cache_mode="refresh" if refresh else "use")
        views = [MCPToolView(server_id=str(server_id), name=t.name,
                             description=getattr(t, "description", "") or "",
                             input_schema=_schema_of(t))
                 for t in (getattr(result, "tools", []) or [])]
        conn.health.tools = len(views)
        return views

    async def list_resources(self, server_id: str) -> list[dict]:
        conn = self._conn(server_id)
        result = await conn.request("list_resources")
        return [{"uri": str(getattr(r, "uri", "")), "name": getattr(r, "name", ""),
                 "description": getattr(r, "description", "") or ""}
                for r in (getattr(result, "resources", []) or [])]

    async def list_prompts(self, server_id: str) -> list[dict]:
        conn = self._conn(server_id)
        result = await conn.request("list_prompts")
        return [{"name": getattr(p, "name", ""),
                 "description": getattr(p, "description", "") or ""}
                for p in (getattr(result, "prompts", []) or [])]

    async def call_tool(self, server_id: str, tool: str, arguments: dict | None = None,
                        *, timeout: float | None = None) -> MCPCallResult:
        conn = self._conn(server_id)
        raw = await conn.request("call_tool", tool, arguments or {}, timeout=timeout)
        text, is_error, structured, blocks = _text_of(raw)
        return MCPCallResult(text=text, is_error=is_error, structured=structured, blocks=blocks)
