"""Гейт консеквентных маршрутов ядра.

Подтверждения — граница безопасности НАД песочницей, браузером и облаком:
именно здесь человек решает, уйдут ли данные в облако, будет ли нажата кнопка
оплаты, попадёт ли траектория в обучающий набор. До этого гейта такие маршруты
ядра были открыты любому, кто дотянулся до порта.

Почему не «разрешить с 127.0.0.1»: ядро публикуется наружу через `tailscale
serve`, который проксирует запрос на loopback. Проверка сетевого положения
после такого прокси всегда истинна и защищает ровно ни от чего — поэтому
решает предъявленный ключ, а не адрес источника.

Fail closed: ключ не настроен → маршрут ОТКЛОНЯЕТСЯ (AuthDenied). Открытое по
умолчанию подтверждение хуже неработающей кнопки: неработающую кнопку видно.
"""
from __future__ import annotations

import hmac

from fastapi import Request

from .config import settings
from .errors import AuthDenied


def _presented(request: Request) -> str:
    """Ключ из запроса: `Authorization: Bearer <key>` либо `X-Bossman-Key`."""
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return (request.headers.get("x-bossman-key") or "").strip()


async def require_core_key(request: Request) -> None:
    """Зависимость FastAPI: пустить дальше только с верным ключом ядра.

    Сравнение — постоянного времени; в сообщении об ошибке нет ни ключа, ни его
    длины, ни части (иначе гейт сам становится оракулом для подбора).
    """
    expected = settings.core_api_key
    if not expected:
        raise AuthDenied("BOSSMAN_CORE_API_KEY не задан — консеквентные маршруты ядра закрыты")
    if not hmac.compare_digest(expected, _presented(request)):
        raise AuthDenied("неверный ключ доступа к ядру")
