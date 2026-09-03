"""Точка входа: `bcc` поднимает Control API на 127.0.0.1:8800.

Наружу сервис не открывается: за пределы машины — только через VPN/Tailscale.
Для uvicorn-фабрики: `uvicorn --factory bcc.app:create`.
"""
from __future__ import annotations

import argparse
from typing import Sequence

import uvicorn
from fastapi import FastAPI

from . import __version__
from .api import create_app
from .config import settings


def create() -> FastAPI:
    """Фабрика приложения (данные и токен создаются при первом вызове)."""
    return create_app(settings)


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
    print(f"[bcc] Command Center: http://{host}:{port}", flush=True)
    uvicorn.run(application, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
