"""Точка входа: `bcc` поднимает Control API на 127.0.0.1:8800.

Наружу сервис не открывается: за пределы машины — только через VPN/Tailscale.
Для uvicorn-фабрики: `uvicorn --factory bcc.app:create`.
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from .api import create_app
from .config import settings


def create() -> FastAPI:
    """Фабрика приложения (данные и токен создаются при первом вызове)."""
    return create_app(settings)


def main() -> None:
    settings.ensure_dirs()
    application = create()
    print(f"[bcc] Command Center: http://{settings.host}:{settings.port}", flush=True)
    uvicorn.run(application, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
