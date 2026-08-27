"""Scheduler: catch-up после простоя срабатывает один раз, once — самоотключается."""
from __future__ import annotations

from datetime import timedelta

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
    assert first_run_at(sched, now).day == (now + timedelta(days=1)).day
    assert next_run_at(sched, now).hour == 9 and next_run_at(sched, now).minute == 30
