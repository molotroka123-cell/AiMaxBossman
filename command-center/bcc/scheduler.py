"""Scheduler (раздел 5): once | interval | daily, тик раз в 30 с, catch-up после reboot.

Пропущенное за время простоя срабатывает ОДИН раз: next_run_at считается от «сейчас»,
а не догоняется по всем пропущенным слотам.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import sqlalchemy as sa

from .db import Database, fetch_one, schedules as sch_t, tasks as tasks_t, rows_dicts, utcnow
from .engine import TaskEngine
from .events import EventBus

TICK_SECONDS = 30.0


class Scheduler:
    def __init__(self, db: Database, bus: EventBus, engine: TaskEngine, *,
                 tick_seconds: float = TICK_SECONDS):
        self.db = db
        self.bus = bus
        self.engine = engine
        self.tick_seconds = tick_seconds
        self.last_tick: float = 0.0        # для health в /api/system

    # ---------- CRUD ----------

    async def list_schedules(self) -> list[dict]:
        async with self.db.session() as s:
            res = await s.execute(sa.select(sch_t).order_by(sch_t.c.id))
            return rows_dicts(res.fetchall())

    async def create(self, **values) -> dict:
        values = {k: v for k, v in values.items() if v is not None}
        values.setdefault("enabled", True)
        if not values.get("next_run_at"):
            values["next_run_at"] = first_run_at(values, utcnow())
        async with self.db.session() as s:
            res = await s.execute(sa.insert(sch_t).values(**values))
            sid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, sch_t, sid)
        await self.bus.emit("schedule.created", id=sid, name=values.get("name"))
        return row or {}

    async def update(self, schedule_id: int, **values) -> dict | None:
        values = {k: v for k, v in values.items() if v is not None}
        async with self.db.session() as s:
            if values:
                await s.execute(sa.update(sch_t).where(sch_t.c.id == schedule_id).values(**values))
                await s.commit()
            return await fetch_one(s, sch_t, schedule_id)

    async def delete(self, schedule_id: int) -> bool:
        async with self.db.session() as s:
            res = await s.execute(sa.delete(sch_t).where(sch_t.c.id == schedule_id))
            await s.commit()
        return bool(res.rowcount)

    # ---------- тик ----------

    async def loop(self) -> None:
        while True:
            self.last_tick = time.monotonic()
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.bus.emit("scheduler.error", message=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(self.tick_seconds)

    async def tick_once(self, now: datetime | None = None) -> list[int]:
        """Сработавшие расписания → task+run; возвращает id созданных задач."""
        now = now or utcnow()
        self.last_tick = time.monotonic()
        async with self.db.session() as s:
            res = await s.execute(sa.select(sch_t).where(
                sch_t.c.enabled.is_(True),
                sch_t.c.next_run_at.isnot(None),
                sch_t.c.next_run_at <= now).order_by(sch_t.c.id))
            due = rows_dicts(res.fetchall())

        created: list[int] = []
        for schedule in due:
            task_id = await self._fire(schedule, now)
            if task_id:
                created.append(task_id)
        return created

    async def _fire(self, schedule: dict, now: datetime) -> int | None:
        template = schedule.get("task_template") or {}
        nxt = next_run_at(schedule, now)
        async with self.db.session() as s:
            # сначала переносим next_run_at: даже при сбое ниже расписание не зациклится
            upd = await s.execute(sa.update(sch_t).where(
                sch_t.c.id == schedule["id"],
                sch_t.c.next_run_at == schedule["next_run_at"]).values(
                next_run_at=nxt, last_fired_at=now,
                enabled=False if schedule["kind"] == "once" else schedule["enabled"]))
            await s.commit()
            if not upd.rowcount:      # уже сработало в другом тике
                return None
            res = await s.execute(sa.insert(tasks_t).values(
                title=template.get("title") or schedule["name"],
                prompt=template.get("prompt") or "",
                agent_id=template.get("agent_id"),
                priority=int(template.get("priority") or 5),
                max_retries=int(template.get("max_retries") or 2),
                schedule_id=schedule["id"],
                status="draft", created_at=utcnow(), updated_at=utcnow()))
            task_id = int(res.inserted_primary_key[0])
            await s.commit()
        await self.bus.emit("task.created", task_id=task_id, schedule_id=schedule["id"],
                            title=template.get("title") or schedule["name"])
        await self.engine.enqueue(task_id)
        await self.bus.emit("schedule.fired", id=schedule["id"], task_id=task_id,
                            next_run_at=nxt.isoformat() if nxt else None)
        return task_id


def first_run_at(schedule: dict, now: datetime) -> datetime | None:
    """Первое срабатывание при создании расписания."""
    kind = schedule.get("kind")
    if kind == "once":
        return schedule.get("at_time") or now
    if kind == "interval":
        minutes = int(schedule.get("interval_minutes") or 0)
        return now + timedelta(minutes=minutes) if minutes > 0 else None
    if kind == "daily":
        return _next_daily(schedule.get("daily_time") or "09:00", now)
    return None


def next_run_at(schedule: dict, now: datetime) -> datetime | None:
    """Следующее срабатывание после текущего (catch-up: считаем от now)."""
    kind = schedule.get("kind")
    if kind == "once":
        return None
    if kind == "interval":
        minutes = int(schedule.get("interval_minutes") or 0)
        return now + timedelta(minutes=minutes) if minutes > 0 else None
    if kind == "daily":
        return _next_daily(schedule.get("daily_time") or "09:00", now, strictly_after=True)
    return None


def _next_daily(daily_time: str, now: datetime, strictly_after: bool = False) -> datetime:
    hour, _, minute = daily_time.partition(":")
    target = now.replace(hour=int(hour or 0), minute=int(minute or 0), second=0, microsecond=0)
    if target < now or (strictly_after and target <= now):
        target += timedelta(days=1)
    return target
