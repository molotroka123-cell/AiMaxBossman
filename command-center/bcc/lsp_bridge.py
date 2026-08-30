"""Безопасный async LSP JSON-RPC мост (code intelligence).

Адаптировано из code_candidates/lsp_bridge.py под Command Center. Встраивается в
СУЩЕСТВУЮЩИЙ tool-registry (см. features/code_intel.py) — второго реестра нет.

Безопасность:
* argv-only запуск языкового сервера (никакого shell=True / строки команды);
* bounded payload (max_message_bytes), timeout на каждый запрос;
* заголовок Content-Length ограничен, тело — по длине;
* graceful shutdown: shutdown→exit→terminate→kill;
* workspace обязан существовать и быть каталогом.

Только чтение: definition/references/hover/symbols/diagnostics. Ничего не мутирует.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LSPError(RuntimeError):
    pass


@dataclass(frozen=True)
class LSPConfig:
    argv: tuple[str, ...]
    workspace: Path
    timeout_s: float = 8.0
    max_message_bytes: int = 4 * 1024 * 1024


class LSPClient:
    # LSP method → ключ провайдера в server capabilities (для negotiation)
    _CAP_KEY = {
        "textDocument/definition": "definitionProvider",
        "textDocument/references": "referencesProvider",
        "textDocument/hover": "hoverProvider",
        "textDocument/documentSymbol": "documentSymbolProvider",
        "textDocument/implementation": "implementationProvider",
        "workspace/symbol": "workspaceSymbolProvider",
    }

    def __init__(self, config: LSPConfig) -> None:
        if not config.argv:
            raise ValueError("empty LSP argv")
        self.cfg = config
        self.proc: asyncio.subprocess.Process | None = None
        self._id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._diagnostics: dict[str, list] = {}
        self.capabilities: dict = {}   # заполняется из ответа initialize

    async def start(self) -> None:
        ws = self.cfg.workspace.resolve(strict=True)
        if not ws.is_dir():
            raise ValueError("workspace must be directory")
        self.proc = await asyncio.create_subprocess_exec(
            *self.cfg.argv, cwd=str(ws),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        self._reader = asyncio.create_task(self._read_loop())
        init = await self.request("initialize", {
            "processId": None, "rootUri": ws.as_uri(),
            "capabilities": {"textDocument": {
                "definition": {}, "references": {}, "hover": {},
                "documentSymbol": {}, "implementation": {}, "publishDiagnostics": {}},
                "workspace": {"symbol": {}}}})
        # capability negotiation: запоминаем, что сервер РЕАЛЬНО умеет
        caps = (init or {}).get("capabilities") if isinstance(init, dict) else None
        self.capabilities = caps if isinstance(caps, dict) else {}
        await self.notify("initialized", {})

    def supports(self, method: str) -> bool:
        """Объявляет ли сервер поддержку метода.

        Если сервер вообще не прислал capabilities (напр. минимальный сервер) —
        оптимистично разрешаем (unknown → try). Если capabilities есть, но
        нужный провайдер явно выключен/отсутствует — отказ (не дёргаем зря)."""
        if not self.capabilities:
            return True
        key = self._CAP_KEY.get(method)
        if key is None:
            return True
        return bool(self.capabilities.get(key))

    @staticmethod
    def normalize_locations(result) -> list[dict]:
        """Единая форма для definition/references/implementation.

        LSP возвращает Location {uri,range}, LocationLink {targetUri,targetRange},
        один объект, список или None — нормализуем в list[{uri, range}]."""
        if result is None:
            return []
        items = result if isinstance(result, list) else [result]
        out: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if "targetUri" in it:          # LocationLink
                out.append({"uri": it.get("targetUri"),
                            "range": it.get("targetSelectionRange") or it.get("targetRange")})
            elif "uri" in it:              # Location
                out.append({"uri": it.get("uri"), "range": it.get("range")})
        return out

    async def close(self) -> None:
        if not self.proc:
            return
        try:
            await self.request("shutdown", None)
        except Exception:
            pass
        try:
            await self.notify("exit", None)
        except Exception:
            pass
        if self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 2)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
        if self._reader:
            self._reader.cancel()

    # ---- JSON-RPC ----

    async def request(self, method: str, params: Any):
        if not self.proc or self.proc.returncode is not None:
            raise LSPError("not running")
        i = self._id
        self._id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        await self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, self.cfg.timeout_s)
        finally:
            self._pending.pop(i, None)

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ---- read-only queries ----

    def _require(self, method: str) -> None:
        if not self.supports(method):
            raise LSPError(f"server does not advertise support for {method}")

    async def symbols(self, uri: str):
        self._require("textDocument/documentSymbol")
        return await self.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})

    async def workspace_symbols(self, query: str):
        self._require("workspace/symbol")
        return await self.request("workspace/symbol", {"query": query})

    async def definition(self, uri, line, char):
        self._require("textDocument/definition")
        return await self._pos("textDocument/definition", uri, line, char)

    async def implementation(self, uri, line, char):
        self._require("textDocument/implementation")
        return await self._pos("textDocument/implementation", uri, line, char)

    async def hover(self, uri, line, char):
        self._require("textDocument/hover")
        return await self._pos("textDocument/hover", uri, line, char)

    async def references(self, uri, line, char):
        self._require("textDocument/references")
        return await self.request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": {"line": line, "character": char},
            "context": {"includeDeclaration": False}})

    def diagnostics(self, uri: str) -> list:
        return list(self._diagnostics.get(uri, []))

    async def _pos(self, method, uri, line, char):
        if line < 0 or char < 0:
            raise ValueError("negative position")
        return await self.request(method, {
            "textDocument": {"uri": uri}, "position": {"line": line, "character": char}})

    # ---- transport ----

    async def _send(self, payload) -> None:
        if not self.proc or not self.proc.stdin:
            raise LSPError("not running")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if len(body) > self.cfg.max_message_bytes:
            raise LSPError("message too large")
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        await self.proc.stdin.drain()

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await self._read_message()
                if msg is None:
                    break
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(LSPError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
                elif msg.get("method") == "textDocument/publishDiagnostics":
                    p = msg.get("params") or {}
                    uri, diag = p.get("uri"), p.get("diagnostics") or []
                    if isinstance(uri, str) and isinstance(diag, list):
                        self._diagnostics[uri] = diag[:2000]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — уведомляем ждущих, не роняем процесс
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(LSPError(f"read failed: {exc}"))

    async def _read_message(self):
        if not self.proc or not self.proc.stdout:
            return None
        length = None
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return None
            if len(line) > 8192:
                raise LSPError("oversized header")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii", "strict").partition(":")
            if key.lower() == "content-length":
                length = int(value.strip())
        if length is None or length < 0 or length > self.cfg.max_message_bytes:
            raise LSPError("bad content length")
        raw = await self.proc.stdout.readexactly(length)
        msg = json.loads(raw.decode())
        if not isinstance(msg, dict):
            raise LSPError("invalid payload")
        return msg
