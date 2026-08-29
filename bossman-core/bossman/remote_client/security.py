"""FastAPI-зависимости Stage 6 — здесь ЖИВЁТ гейт скоупов.

`require_scope(scope)` — единственная точка, где решается доступ устройства к
маршруту. Каждый защищённый роут объявляет требуемый скоуп через Depends; без
нужного скоупа запрос падает ScopeDenied (403) ДО тела обработчика. Так
chat-устройство физически не может дойти до admin/approve-маршрутов: гейт
срабатывает на этапе разрешения зависимостей, обработчик не вызывается.

Порядок в гейте:
  1. authenticate() — иначе AuthDenied/DeviceRevoked;
  2. проверка скоупа — иначе ScopeDenied.
Аутентификация всегда предшествует проверке прав (нельзя узнать скоупы, не
подтвердив личность устройства).
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request

from ..errors import AuthDenied, ScopeDenied
from .auth import Principal
from .service import get_service


async def authenticate_request(request: Request) -> Principal:
    """Зависимость: только аутентификация (без требования конкретного скоупа).
    Используется там, где достаточно быть валидным неотозванным устройством."""
    authorization = request.headers.get("authorization")
    return await get_service().authenticate(authorization)


def require_scope(scope: str) -> Callable[[Request], Awaitable[Principal]]:
    """Фабрика зависимости: аутентифицировать и потребовать конкретный скоуп."""

    async def _dependency(request: Request) -> Principal:
        principal = await authenticate_request(request)
        if not principal.has_scope(scope):
            # Никаких токенов в сообщении — только требуемый скоуп.
            raise ScopeDenied(f"scope '{scope}' required for this route")
        return principal

    _dependency.__name__ = f"require_scope[{scope}]"
    return _dependency


async def require_device_token(request: Request) -> Principal:
    """Зависимость для /remote/auth: открыть сессию можно только по токену
    УСТРОЙСТВА, не по токену сессии (сессия не порождает сессию)."""
    principal = await authenticate_request(request)
    if principal.session_id is not None:
        raise AuthDenied("device token required to open a session")
    return principal
