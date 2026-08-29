"""Stage 8 — плоскость egress: реальный барьер для режима ALLOWLIST.

OFFLINE обеспечивается рантаймом (сетевой namespace без интерфейсов). Но
ALLOWLIST нельзя реализовать «намерением»: песочнице нужен ЕДИНСТВЕННЫЙ выход
наружу — локальный форвард-прокси control-plane'а, который на каждый CONNECT
спрашивает `NetworkGuard` и отказывает по умолчанию.

Свойства:
- default deny: неизвестный/незаявленный хост, приватная сеть, metadata,
  control-plane — отказ (решение принимает NetworkGuard, а не песочница);
- решения пишутся в траекторию (allow/deny + причина);
- прокси слушает ТОЛЬКО на loopback и знает лишь свою песочницу;
- падение прокси = нет egress (fail closed), а не открытая сеть.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .. import obs
from .models import NetworkMode, SandboxPolicy
from .network import NetworkGuard

log = obs.get_logger("bossman.sandbox.egress")

_CONNECT_OK = b"HTTP/1.1 200 Connection Established\r\n\r\n"
_DENIED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


@dataclass(slots=True)
class EgressDecisionLog:
    host: str
    port: int
    allowed: bool
    reason: str


def parse_connect(line: bytes) -> tuple[str, int] | None:
    """Разобрать 'CONNECT host:port HTTP/1.1'. Возвращает None на любом ином методе:
    прокси умеет только туннель, чтобы не стать открытым релеем."""
    try:
        parts = line.decode("latin-1", errors="replace").split()
    except Exception:  # noqa: BLE001
        return None
    if len(parts) < 2 or parts[0].upper() != "CONNECT":
        return None
    target = parts[1]
    if target.startswith("["):                      # [::1]:443
        host, _, rest = target.partition("]")
        host = host[1:]
        port = rest.lstrip(":")
    else:
        host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        return None
    return host, int(port)


class EgressProxy:
    """Локальный CONNECT-прокси, единственный разрешённый выход песочницы."""

    def __init__(self, policy: SandboxPolicy, *, guard: NetworkGuard | None = None,
                 recorder=None, host: str = "127.0.0.1", port: int = 0) -> None:
        self.policy = policy
        self.guard = guard or NetworkGuard()
        self.recorder = recorder
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self.decisions: list[EgressDecisionLog] = []

    @property
    def address(self) -> tuple[str, int] | None:
        if self._server is None:
            return None
        sock = self._server.sockets[0]
        return sock.getsockname()[:2]

    async def start(self) -> None:
        # В OFFLINE прокси не поднимается вовсе: выхода нет by design.
        if self.policy.network_mode == NetworkMode.OFFLINE:
            raise RuntimeError("egress proxy must not run in OFFLINE mode")
        self._server = await asyncio.start_server(self._handle, self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._server = None

    def _record(self, host: str, port: int, allowed: bool, reason: str) -> None:
        self.decisions.append(EgressDecisionLog(host, port, allowed, reason))
        if self.recorder is not None:
            try:
                self.recorder.network(host, allowed, reason)
            except Exception:  # noqa: BLE001
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_w = None
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            target = parse_connect(line)
            if target is None:
                writer.write(_BAD)
                await writer.drain()
                return
            host, port = target
            # Дочитать и выбросить остальные заголовки запроса.
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=10)
                if h in (b"\r\n", b"\n", b""):
                    break

            decision = self.guard.decide(host, self.policy, port)
            self._record(host, port, decision.allowed, decision.reason)
            if not decision.allowed:
                writer.write(_DENIED)
                await writer.drain()
                return

            upstream_r, upstream_w = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15)
            writer.write(_CONNECT_OK)
            await writer.drain()
            await asyncio.gather(
                _pipe(reader, upstream_w),
                _pipe(upstream_r, writer),
                return_exceptions=True,
            )
        except Exception as exc:  # noqa: BLE001 — сбой прокси = нет egress, не открытая сеть
            log.warning("egress proxy error: %s", exc)
        finally:
            for w in (upstream_w, writer):
                if w is None:
                    continue
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:  # noqa: BLE001
        pass
