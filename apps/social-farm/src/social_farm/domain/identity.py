"""Идентификаторы и ключи идемпотентности.

Спека даёт формулу ключа только для публикации, а требует его для ЛЮБОГО
внешнего эффекта (`DIGEST_CORE` G3). Здесь одна схема на все действия.

Ключ выводится из того, что делает действие, а не из случайности. Случайный
ключ не защищает ни от чего: при повторе он будет другим, и провайдер увидит
второе действие. Смысл ключа именно в том, что повтор одного и того же нашего
намерения даёт то же значение.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Any

from .content import canonical_json

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"      # Crockford base32, без похожих букв


def new_id(prefix: str = "") -> str:
    """Сортируемый по времени идентификатор: время в миллисекундах + случайность.

    Сортируемость не украшение: она делает выборку «последние работы аккаунта»
    индексируемой без отдельной колонки времени и делает логи читаемыми глазами.
    """
    ms = int(time.time() * 1000)
    body = ""
    for _ in range(10):
        ms, rem = divmod(ms, 32)
        body = _ALPHABET[rem] + body
    tail = "".join(_ALPHABET[b % 32] for b in os.urandom(10))
    return f"{prefix}{'_' if prefix else ''}{body}{tail}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def idempotency_key(*, account_id: str, capability: str, payload: Any,
                    target_ref: str = "", schedule_at: str | None = None) -> str:
    """Единый ключ для любого внешнего эффекта.

    `{account_id}:{capability}:{target_ref}:{payload_hash}:{slot}`

    `target_ref` — идентификатор объекта у провайдера, к которому относится
    действие (комментарий, диалог, публикация). Для действий без цели он пуст.
    `slot` — момент запланированного исполнения в UTC либо `now`: две
    публикации одного и того же на разное время — это два разных намерения, и
    ключ обязан их различать.
    """
    if not account_id or not capability:
        raise ValueError("ключ идемпотентности требует account_id и capability")
    slot = "now"
    if schedule_at:
        text = str(schedule_at).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            slot = moment.astimezone(timezone.utc).isoformat()
        except ValueError:
            slot = str(schedule_at)
    return f"{account_id}:{capability}:{target_ref}:{payload_hash(payload)}:{slot}"


__all__ = ["idempotency_key", "new_id", "payload_hash", "utc_now"]
