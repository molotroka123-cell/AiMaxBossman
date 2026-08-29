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
    # ЭТАП 4–7: подмешиваем текущий бандл correlation (request_id/task_id/run_id/
    # job_id/device_id), чтобы WS-событие и лог-строка несли одинаковые id.
    # Явные поля в data имеют приоритет над бандлом.
    from . import correlation
    cid = correlation.current()
    payload = {"kind": kind, "ts": datetime.now(timezone.utc).isoformat()}
    payload.update(cid)
    payload.update(data)
    msg = json.dumps(payload, ensure_ascii=False, default=str)
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            _subscribers.discard(q)  # отставший клиент переподключится и перечитает состояние
