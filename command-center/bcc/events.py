"""Шина событий: подписчики WS + история в таблице events (лента активности).

Живой поток получает всё; в БД кладутся только «содержательные» события —
метрики и построчный лог run'а идут своими таблицами (system_metrics, run_events),
дублировать их в ленте активности бессмысленно.
"""
from __future__ import annotations

import asyncio
from typing import Any

import sqlalchemy as sa

from .db import Database, events as events_t, rows_dicts, utcnow

# эти виды не пишем в историю: у них есть свои таблицы и своя частота
TRANSIENT = {"system.metrics", "run.log"}


class EventBus:
    def __init__(self, db: Database | None = None):
        self.db = db
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def emit(self, kind: str, /, **data: Any) -> dict:
        # kind — только позиционный: в data встречаются свои поля с именем kind
        """Разослать событие подписчикам и (если оно содержательное) записать в историю."""
        # kind/ts всегда наши: поле данных с тем же именем не должно подменять вид события
        msg = {**data, "kind": kind, "ts": utcnow().isoformat()}
        if self.db is not None and kind not in TRANSIENT:
            try:
                async with self.db.session() as s:
                    await s.execute(sa.insert(events_t).values(kind=kind, ts=utcnow(), data=data))
                    await s.commit()
            except Exception:  # история не должна ронять основную работу
                pass
        self.publish(msg)
        return msg

    def publish(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # отставший клиент отключается и после переподключения перечитает состояние
                self._subscribers.discard(q)

    async def recent(self, limit: int = 50) -> list[dict]:
        if self.db is None:
            return []
        async with self.db.session() as s:
            res = await s.execute(
                sa.select(events_t).order_by(events_t.c.id.desc()).limit(limit))
            return rows_dicts(res.fetchall())
