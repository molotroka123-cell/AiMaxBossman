"""Шина событий: подписчики WS + история в таблице events (лента активности).

Живой поток получает всё; в БД кладутся только «содержательные» события —
метрики и построчный лог run'а идут своими таблицами (system_metrics, run_events),
дублировать их в ленте активности бессмысленно.
"""
from __future__ import annotations

import asyncio
from typing import Any

import sqlalchemy as sa

from .db import Database, events as events_t, rows_dicts, run_events as run_events_t, utcnow
from .plugin_security import redact
from .trace import get_trace_id

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
        # Секреты не попадают ни в таблицу events, ни в WS-ленту: чистка по
        # именам ключей (api_key/token/password/…) на любой глубине payload —
        # ДО персиста и ДО broadcast, чтобы оба пути видели одно и то же.
        data = redact(data)
        # Время события считается ОДИН раз. Раньше utcnow() вызывался дважды —
        # отдельно для рассылки и отдельно для записи в историю, — и одно и то
        # же событие приходило с разным временем в живой ленте и в /activity.
        # Расхождение видно владельцу, а сверить два пути между собой (чтобы не
        # считать событие дважды) при разном времени вообще невозможно.
        now = utcnow()
        # TRUTH-003 §14: trace_id из контекста исполнения, если вызывающий не передал свой
        if "trace_id" not in data:
            tid = get_trace_id()
            if tid:
                data["trace_id"] = tid
        # kind/ts всегда наши: поле данных с тем же именем не должно подменять вид события
        msg = {**data, "kind": kind, "ts": now.isoformat()}
        if self.db is not None and kind not in TRANSIENT:
            try:
                async with self.db.session() as s:
                    await s.execute(sa.insert(events_t).values(kind=kind, ts=now, data=data))
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

    async def by_trace(self, trace_id: str, limit: int = 500) -> list[dict]:
        """Цепочка событий одного действия (по trace_id в payload)."""
        if self.db is None:
            return []
        async with self.db.session() as s:
            res = await s.execute(sa.select(events_t).order_by(events_t.c.id.asc()).limit(5000))
            rows = rows_dicts(res.fetchall())
        return [r for r in rows if isinstance(r.get("data"), dict) and r["data"].get("trace_id") == trace_id][-limit:]

    async def prune(self, *, max_age_days: int = 14, max_rows: int = 200_000) -> dict[str, int]:
        """TRUTH-003 §14: ограниченное хранение — по возрасту и по числу строк (events и run_events).
        Возвращает, сколько строк удалено. Ничего не удаляет, если БД нет."""
        if self.db is None:
            return {"events": 0, "run_events": 0}
        from datetime import timedelta
        cutoff = utcnow() - timedelta(days=max(1, int(max_age_days)))
        removed = {"events": 0, "run_events": 0}
        async with self.db.session() as s:
            for name, tbl, ts_col in (("events", events_t, events_t.c.ts), ("run_events", run_events_t, run_events_t.c.ts)):
                res = await s.execute(sa.delete(tbl).where(ts_col < cutoff))
                removed[name] += int(res.rowcount or 0)
                total = int((await s.execute(sa.select(sa.func.count()).select_from(tbl))).scalar() or 0)
                if total > max_rows:
                    keep_from = (await s.execute(sa.select(tbl.c.id).order_by(tbl.c.id.desc()).offset(max_rows).limit(1))).scalar()
                    if keep_from is not None:
                        res = await s.execute(sa.delete(tbl).where(tbl.c.id <= keep_from))
                        removed[name] += int(res.rowcount or 0)
            await s.commit()
        return removed

    async def recent(self, limit: int = 50) -> list[dict]:
        if self.db is None:
            return []
        async with self.db.session() as s:
            res = await s.execute(
                sa.select(events_t).order_by(events_t.c.id.desc()).limit(limit))
            return rows_dicts(res.fetchall())
