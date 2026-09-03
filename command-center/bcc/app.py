"""Точка входа: `bcc` поднимает Control API на 127.0.0.1:8800.

Наружу сервис не открывается: за пределы машины — только через VPN/Tailscale.
Для uvicorn-фабрики: `uvicorn --factory bcc.app:create`.
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
from typing import Sequence

import uvicorn
from fastapi import FastAPI

from . import __version__
from .api import create_app
from .config import settings


def create() -> FastAPI:
    """Фабрика приложения (данные и токен создаются при первом вызове)."""
    return create_app(settings)


def is_loopback(host: str) -> bool:
    """Адрес виден только с этой машины?

    Имя (``localhost``) и любая запись адреса (``127.1``, ``::1``, IPv4-mapped)
    должны решаться одинаково, поэтому сравниваем разобранный адрес, а не строку.
    """
    name = (host or "").strip().strip("[]").lower()
    if name in {"", "localhost"}:
        return True
    try:
        addr = ipaddress.ip_address(name)
    except ValueError:
        try:
            # Короткие формы (127.1, 0x7f000001) ipaddress не разбирает, а ядро — да.
            addr = ipaddress.IPv4Address(socket.inet_aton(name))
        except (OSError, ValueError):
            return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bcc", description="BOSSMAN Command Center — локальный Control API")
    p.add_argument("--host", default=None, help=f"адрес (по умолчанию {settings.host})")
    p.add_argument("--port", type=int, default=None, help=f"порт (по умолчанию {settings.port})")
    p.add_argument("--version", action="store_true", help="показать версию и выйти")
    return p


def main(argv: Sequence[str] | None = None) -> None:
    """Запуск сервера. ``--help`` и ``--version`` больше не поднимают сервер."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.version:
        print(f"bcc {__version__}", flush=True)
        return
    host, port = args.host or settings.host, args.port or settings.port
    settings.ensure_dirs()
    application = create()
    if not is_loopback(host):
        # Сервис задуман локальным (наружу — только через VPN/Tailscale). Молча
        # открыться на весь мир он не должен: владелец обязан это увидеть.
        print(f"[bcc] ВНИМАНИЕ: адрес {host} доступен не только с этой машины. "
              f"Command Center рассчитан на 127.0.0.1; наружу — через VPN/Tailscale.", flush=True)
    print(f"[bcc] Command Center: http://{host}:{port}", flush=True)
    uvicorn.run(application, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
