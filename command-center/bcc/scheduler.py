"""Scheduler (раздел 5): once | interval | daily, тик раз в 30 с, catch-up после reboot.

Пропущенное за время простоя срабатывает ОДИН раз: next_run_at считается от «сейчас»,
а не догоняется по всем пропущенным слотам.

Время: next_run_at хранится наивным UTC (как всё в системе), а daily_time — это
«ЧЧ:ММ» на часах владельца (форма в вебе отправляет то, что он ввёл, и так же
показывает «Ежедневно в ЧЧ:ММ»). Поэтому daily_time переводится из местного
времени машины в UTC. Раньше оно читалось как UTC, и «09:00» у владельца в
UTC+3 срабатывало в 12:00.
"""
from __future__ import annotations

import asyncio

from .lifecycle import sleep_or_stop, stopping
import time
from datetime import datetime, timedelta, timezone, tzinfo

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
        self.last_error: str | None = None
        # Ставится Services.stop(): петля выходит сама, не будучи
        # оборванной посреди запроса к базе (см. bcc/lifecycle.py).
        self.stop_event: asyncio.Event | None = None

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
        while not stopping(self.stop_event):
            self.last_tick = time.monotonic()
            try:
                await self.tick_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                await self.bus.emit("scheduler.error", message=f"{type(exc).__name__}: {exc}")
            if await sleep_or_stop(self.stop_event, self.tick_seconds):
                return

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


def first_run_at(schedule: dict, now: datetime, *, tz: tzinfo | None = None) -> datetime | None:
    """Первое срабатывание при создании расписания (наивный UTC)."""
    kind = schedule.get("kind")
    if kind == "once":
        return schedule.get("at_time") or now
    if kind == "interval":
        minutes = int(schedule.get("interval_minutes") or 0)
        return now + timedelta(minutes=minutes) if minutes > 0 else None
    if kind == "daily":
        return _next_daily(schedule.get("daily_time") or "09:00", now, tz=tz)
    return None


def next_run_at(schedule: dict, now: datetime, *, tz: tzinfo | None = None) -> datetime | None:
    """Следующее срабатывание после текущего (catch-up: считаем от now)."""
    kind = schedule.get("kind")
    if kind == "once":
        return None
    if kind == "interval":
        minutes = int(schedule.get("interval_minutes") or 0)
        return now + timedelta(minutes=minutes) if minutes > 0 else None
    if kind == "daily":
        return _next_daily(schedule.get("daily_time") or "09:00", now, strictly_after=True, tz=tz)
    return None


def _next_daily(daily_time: str, now: datetime, strictly_after: bool = False, *,
                tz: tzinfo | None = None) -> datetime:
    """Ближайшее «ЧЧ:ММ» по местным часам (``tz``; по умолчанию часы машины)
    после наивного UTC ``now``; результат — наивный UTC."""
    hour, _, minute = daily_time.partition(":")
    utc_now = now.replace(tzinfo=timezone.utc)
    # Без tz: часовой пояс машины, со сменой летнего времени — наивная местная
    # дата переводится в UTC через astimezone(), а не фиксированным сдвигом.
    local_now = utc_now.astimezone(tz) if tz is not None else utc_now.astimezone()
    wall_now = local_now.replace(tzinfo=None)
    target = wall_now.replace(hour=int(hour or 0), minute=int(minute or 0), second=0, microsecond=0)
    if target < wall_now or (strictly_after and target <= wall_now):
        target += timedelta(days=1)
    aware = target.replace(tzinfo=tz) if tz is not None else target.astimezone()
    return aware.astimezone(timezone.utc).replace(tzinfo=None)
