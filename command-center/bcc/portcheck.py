"""Быстрый ответ «на этом loopback-порту никто не слушает».

На Windows connect() на ЗАКРЫТЫЙ порт 127.0.0.1 возвращает ConnectionRefused только через ~2.05 с
(повтор SYN), а не сразу. Поэтому каждый опрос остановленного соседнего приложения стоит секунды:
измерено на этой машине — /api/apps p95 1.25 с, /api/reality/observe p95 1.4 с, а первый тик
reality задерживал готовность на 1.2 с. Список слушающих сокетов (psutil, IP Helper) читается за
~2 мс и отвечает на тот же вопрос без сетевого соединения.

Честность: функция говорит ТОЛЬКО «точно закрыт» (True). Любое сомнение — не удалось прочитать
таблицу сокетов, нет psutil, не loopback — это False, и вызывающий делает прежний сетевой опрос.
"""
from __future__ import annotations

import asyncio
import time

_TTL = 0.5
_cache: tuple[float, frozenset[int]] | None = None


def _listening() -> frozenset[int] | None:
    try:
        import psutil
        return frozenset(c.laddr.port for c in psutil.net_connections(kind="tcp")
                         if c.status == "LISTEN" and c.laddr)
    except Exception:                       # noqa: BLE001 — нет psutil/прав: «не знаю»
        return None


def is_loopback_host(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


async def loopback_port_closed(port: int, host: str = "127.0.0.1") -> bool:
    """True — порт точно никто не слушает (сетевой опрос не нужен). False — «не знаю» или слушают."""
    global _cache
    if not is_loopback_host(host):
        return False
    now = time.monotonic()
    cached = _cache
    if cached is None or now - cached[0] > _TTL:
        ports = await asyncio.to_thread(_listening)
        if ports is None:
            return False
        _cache = cached = (now, ports)
    return int(port) not in cached[1]
