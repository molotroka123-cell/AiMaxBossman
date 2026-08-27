"""Шина событий: всё, что меняется (задачи, подтверждения, модели, проекты),
уходит в WS /events — UI обновляется сам, без опроса."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def emit(kind: str, **data: Any) -> None:
    msg = json.dumps({"kind": kind, "ts": datetime.now(timezone.utc).isoformat(), **data},
                     ensure_ascii=False, default=str)
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            _subscribers.discard(q)  # отставший клиент переподключится и перечитает состояние
