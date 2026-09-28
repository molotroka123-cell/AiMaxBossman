"""Scheduler: catch-up после простоя срабатывает один раз, once — самоотключается."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bcc.db import utcnow

from .conftest import FakeAdapter, client_for, make_settings, start_app
from .helpers import make_stack


async def _agent_id(client) -> int:
    ids = await make_stack(client)
    return ids["agent"]["id"]


async def test_interval_schedule_catch_up_fires_once(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False,
                               adapter_factory=lambda m, p: FakeAdapter())
    async with client_for(app, svc) as client:
        agent_id = await _agent_id(client)
        # next_run_at в прошлом — как после нескольких часов простоя машины
        stale = utcnow() - timedelta(hours=3)
        schedule = (await client.post("/api/schedules", json={
            "name": "каждые 30 минут", "kind": "interval", "interval_minutes": 30,
            "next_run_at": stale.isoformat(),
            "task_template": {"title": "отчёт", "prompt": "сделай отчёт",
                              "agent_id": agent_id, "priority": 3, "max_retries": 1},
        })).json()

        created = await svc.scheduler.tick_once()
        assert len(created) == 1                     # ровно один раз, а не по разу за слот
        assert not await svc.scheduler.tick_once()   # следующий тик уже ничего не находит

        fresh = (await client.get("/api/schedules")).json()[0]
        assert fresh["enabled"] is True
        assert fresh["next_run_at"] > utcnow().isoformat()   # перенесено вперёд от «сейчас»
        assert fresh["last_fired_at"] is not None

        tasks = (await client.get("/api/tasks", params={"status": "queued"})).json()
        spawned = [t for t in tasks if t["schedule_id"] == schedule["id"]]
        assert len(spawned) == 1
        assert spawned[0]["title"] == "отчёт" and spawned[0]["priority"] == 3
        assert spawned[0]["last_run"]["status"] == "queued"
    await svc.stop()


async def test_once_schedule_disables_itself(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False,
                               adapter_factory=lambda m, p: FakeAdapter())
    async with client_for(app, svc) as client:
        agent_id = await _agent_id(client)
        await client.post("/api/schedules", json={
            "name": "разовая", "kind": "once",
            "at_time": (utcnow() - timedelta(minutes=1)).isoformat(),
            "next_run_at": (utcnow() - timedelta(minutes=1)).isoformat(),
            "task_template": {"prompt": "один раз", "agent_id": agent_id},
        })
        assert len(await svc.scheduler.tick_once()) == 1

        fresh = (await client.get("/api/schedules")).json()[0]
        assert fresh["enabled"] is False and fresh["next_run_at"] is None
        assert not await svc.scheduler.tick_once()
    await svc.stop()


async def test_daily_next_run_is_tomorrow_when_time_passed(tmp_path):
    from bcc.scheduler import first_run_at, next_run_at

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    sched = {"kind": "daily", "daily_time": "09:30"}
    # daily_time is the owner's wall clock; with the clock at UTC the old
    # expectations hold exactly (other zones: tests below).
    utc = timezone.utc
    assert first_run_at(sched, now, tz=utc).day == (now + timedelta(days=1)).day
    assert next_run_at(sched, now, tz=utc).hour == 9 and next_run_at(sched, now, tz=utc).minute == 30


def test_daily_time_is_the_owners_local_clock_not_utc():
    """RC19 lifecycle audit P1-7: the web form sends the owner's «09:00»; it was
    read as UTC, so at UTC+3 it fired at 12:00 local."""
    from bcc.scheduler import first_run_at, next_run_at

    msk = timezone(timedelta(hours=3))
    now = datetime(2026, 9, 28, 12, 0)                      # naive UTC = 15:00 at UTC+3
    daily = {"kind": "daily", "daily_time": "09:30"}
    assert next_run_at(daily, now, tz=msk) == datetime(2026, 9, 29, 6, 30)   # tomorrow 09:30 local
    evening = {"kind": "daily", "daily_time": "18:00"}
    assert first_run_at(evening, now, tz=msk) == datetime(2026, 9, 28, 15, 0)  # today 18:00 local
    # the stored UTC instant reads back as the entered wall-clock time
    back = next_run_at(daily, now, tz=msk).replace(tzinfo=timezone.utc).astimezone(msk)
    assert (back.hour, back.minute) == (9, 30)


def test_daily_time_defaults_to_this_machines_clock():
    from bcc.scheduler import next_run_at

    now = utcnow().replace(second=0, microsecond=0)
    nxt = next_run_at({"kind": "daily", "daily_time": "07:45"}, now)
    local = nxt.replace(tzinfo=timezone.utc).astimezone()
    assert (local.hour, local.minute) == (7, 45)
    assert timedelta(0) < nxt - now <= timedelta(days=1, hours=1)
