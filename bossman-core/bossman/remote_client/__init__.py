"""Stage 6 — Private Remote Client backend.

Публичная поверхность пакета:
  * `DeviceRegistry`   — синхронный in-memory реестр (контракт приёмочного теста);
  * `build_subsystem`  — фабрика подсистемы для реестра lifecycle;
  * `router`           — FastAPI APIRouter (/remote/*), включается в api.py.

ГРАНИЦА БЕЗОПАСНОСТИ (документируется намеренно): бэкенд предполагает приватный
туннель/TLS-периметр (Tailscale serve и т.п.). Сырые модельные бэкенды НИКОГДА
не публикуются наружу; наружу выходит только этот gated API. Скоупы проверяются
на КАЖДОМ запросе; cloud_policy отсюда неизменяема (маршрутов мутации политики
нет). Подробности — в README.md.
"""
from __future__ import annotations

from .auth import (
    KNOWN_SCOPES,
    SCOPE_ADMIN,
    SCOPE_APPROVE,
    SCOPE_CHAT,
    SCOPE_EVENTS,
    DeviceRegistry,
    Principal,
)
from .router import router
from .service import DeviceService, get_service, reset_service, set_service
from .store import InMemoryDeviceStore, PostgresDeviceStore
from .subsystem import build_subsystem

__all__ = [
    "DeviceRegistry",
    "DeviceService",
    "InMemoryDeviceStore",
    "PostgresDeviceStore",
    "Principal",
    "KNOWN_SCOPES",
    "SCOPE_CHAT",
    "SCOPE_EVENTS",
    "SCOPE_APPROVE",
    "SCOPE_ADMIN",
    "build_subsystem",
    "router",
    "get_service",
    "set_service",
    "reset_service",
]
