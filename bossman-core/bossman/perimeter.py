"""Периметр ядра — Stage 6 скоупы на HTTP/WS маршрутах самого ядра.

Здесь НЕТ второго механизма аутентификации: единственный источник истины —
Stage 6 (`remote_client.security.require_scope` поверх `DeviceService`), тот же,
что защищает /remote/*. Ядро лишь объявляет, какой скоуп нужен какому маршруту:

  chat    — задачи, проекты, поиск, видео-задания, чтение операционных данных;
  events  — подписка на шину событий (WS /events);
  approve — просмотр и решение подтверждений (граница НАД песочницей/облаком);
  admin   — ресурсы, песочница, dev-factory, AI Lab, смена политики агента.

Сетевое положение источника (127.0.0.1, Tailscale) сознательно НЕ является
аутентификацией: за `tailscale serve` любой внешний запрос приходит с loopback.
Приватная сеть — дополнительный слой (defense in depth), а не замена ключам.

Токен НИКОГДА не передаётся в URL (query string попадает в логи и историю).
Для WebSocket, где браузер не умеет ставить Authorization, используется
субпротокол `bossman.bearer.<token>` — он едет заголовком Sec-WebSocket-Protocol
и не попадает ни в URL, ни в access-логи.
"""
from __future__ import annotations

from fastapi import WebSocket

from .errors import AuthDenied, ScopeDenied
from .remote_client.auth import (      # noqa: F401 — реэкспорт для роутеров ядра
    SCOPE_ADMIN,
    SCOPE_APPROVE,
    SCOPE_CHAT,
    SCOPE_EVENTS,
    Principal,
)
from .remote_client.security import require_scope  # noqa: F401 — реэкспорт

WS_SUBPROTOCOL_PREFIX = "bossman.bearer."


def websocket_token(ws: WebSocket) -> tuple[str | None, str | None]:
    """(authorization_header, выбранный субпротокол) из WS-рукопожатия.

    Приоритет — обычный Authorization (не-браузерные клиенты); иначе токен из
    субпротокола `bossman.bearer.<token>`. Возвращённый субпротокол обязателен
    в accept(): иначе браузер сам разорвёт соединение.
    """
    authorization = ws.headers.get("authorization")
    if authorization:
        return authorization, None
    for proto in ws.scope.get("subprotocols") or []:
        if proto.startswith(WS_SUBPROTOCOL_PREFIX):
            token = proto[len(WS_SUBPROTOCOL_PREFIX):]
            if token:
                return f"Bearer {token}", proto
    return None, None


async def authenticate_websocket(ws: WebSocket, scope: str) -> tuple[Principal, str | None]:
    """Аутентифицировать WS ДО accept()/подписки. Бросает AuthDenied/ScopeDenied —
    вызывающий закрывает соединение кодом 1008, не открыв подписку."""
    from .remote_client.service import get_service

    authorization, chosen = websocket_token(ws)
    if not authorization:
        raise AuthDenied("websocket requires bearer token (header or subprotocol)")
    principal = await get_service().authenticate(authorization)
    if not principal.has_scope(scope):
        raise ScopeDenied(f"scope '{scope}' required for this stream")
    return principal, chosen
