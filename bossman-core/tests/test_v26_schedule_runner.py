"""V2.6 раздел 21 — schedule_runner: исполнитель AgentSpec.schedule.

Реальный формат поля — 5-полевой cron (agents/fresh-vibes/agent.yaml:
"*/15 8-20 * * 1-6"); никакого нового формата. Всё без Postgres/Redis:
db и enqueue — фейки, время инжектится. Инварианты: default OFF,
max_fires_per_day, без перекрытий, один сбой не убивает петлю.
2026-08-31 — понедельник (cron dow=1), 2026-08-30 — воскресенье (dow=0).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from bossman import schedule_runner
from bossman.schedule_runner import (ENABLE_ENV, ScheduleRunner, cron_matches,
                                     next_fire, parse_cron)

CRON = "*/15 8-20 * * 1-6"  # боевой пример из agents/fresh-vibes/agent.yaml


# ---------------- разбор реального формата ----------------

def test_parse_real_agent_yaml_format():
    c = parse_cron(CRON)
    assert c.minutes == frozenset({0, 15, 30, 45})
    assert c.hours == frozenset(range(8, 21))
    assert c.dows == frozenset(range(1, 7))     # пн..сб, без воскресенья
    assert c.day_star is True and c.dow_star is False


def test_dow_seven_normalized_to_sunday():
    c = parse_cron("0 12 * * 7")
    assert c.dows == frozenset({0})
    assert cron_matches(c, datetime(2026, 8, 30, 12, 0))   # воскресенье


def test_invalid_expressions_raise():
    with pytest.raises(ValueError):
        parse_cron("every 15m")          # выдуманный формат — не наш
    with pytest.raises(ValueError):
        parse_cron("61 * * * *")
    with pytest.raises(ValueError):
        parse_cron("* * * *")


# ---------------- детерминированное «когда» ----------------

def test_cron_matches_deterministic():
    assert cron_matches(CRON, datetime(2026, 8, 31, 9, 15))        # пн 09:15
    assert not cron_matches(CRON, datetime(2026, 8, 31, 9, 16))    # не кратно 15
    assert not cron_matches(CRON, datetime(2026, 8, 31, 21, 0))    # час вне 8-20
    assert not cron_matches(CRON, datetime(2026, 8, 30, 9, 15))    # воскресенье


def test_next_fire_within_day():
    assert next_fire(CRON, datetime(2026, 8, 31, 8, 7)) == datetime(2026, 8, 31, 8, 15)


def test_next_fire_skips_sunday():
    # суббота 20:50 → 20:45 уже прошло, воскресенье исключено → пн 08:00
    assert next_fire(CRON, datetime(2026, 8, 29, 20, 50)) == datetime(2026, 8, 31, 8, 0)


def test_next_fire_strictly_after_now_daily():
    assert next_fire("0 9 * * *", datetime(2026, 8, 31, 9, 0)) == datetime(2026, 9, 1, 9, 0)


# ---------------- фейки вместо Postgres/Redis ----------------

class FakeDB:
    def __init__(self, overlap: bool = False):
        self.overlap = overlap
        self.raise_once = False
        self.inserted: list[tuple] = []
        self._next_id = 1

    async def fetchrow(self, sql: str, *args):
        if self.raise_once:
            self.raise_once = False
            raise RuntimeError("БД мигнула")
        if sql.lstrip().upper().startswith("SELECT"):
            return {"id": 999} if self.overlap else None
        assert "source" in sql and "'schedule'" in sql
        self.inserted.append(args)
        tid, self._next_id = self._next_id, self._next_id + 1
        return {"id": tid}


def _runner(agents: dict, db: FakeDB, enqueued: list[int], **kw) -> ScheduleRunner:
    async def enqueue(task_id: int) -> None:
        enqueued.append(task_id)
    return ScheduleRunner(lambda: agents, fetchrow=db.fetchrow, enqueue=enqueue, **kw)


def _agents(schedule: str = CRON) -> dict:
    return {"fresh-vibes": SimpleNamespace(schedule=schedule)}


# ---------------- default OFF: поведение ядра не меняется ----------------

def test_disabled_by_default_loop_exits_immediately(monkeypatch):
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    called = {"n": 0}

    def load_agents():
        called["n"] += 1
        return {}

    runner = ScheduleRunner(load_agents)
    asyncio.run(runner.loop())          # вернулась сразу, ничего не тронула
    assert called["n"] == 0
    assert runner.enabled() is False


# ---------------- срабатывание: существующие входы, без второй очереди ----------------

def test_due_fire_inserts_schedule_task_and_enqueues():
    db, enq = FakeDB(), []
    runner = _runner(_agents(), db, enq)
    now = datetime(2026, 8, 31, 9, 15)                 # пн 09:15 — due
    fired = asyncio.run(runner.tick(now=now))
    assert fired == [1] and enq == [1]
    agent, text = db.inserted[0]
    assert agent == "fresh-vibes"
    assert text.startswith("[schedule]") and CRON in text
    # та же минута — повторного срабатывания нет
    assert asyncio.run(runner.tick(now=now.replace(second=40))) == []
    # следующая подходящая минута — срабатывает снова
    assert asyncio.run(runner.tick(now=datetime(2026, 8, 31, 9, 30))) == [2]
    assert enq == [1, 2]


def test_not_due_no_fire():
    db, enq = FakeDB(), []
    runner = _runner(_agents(), db, enq)
    assert asyncio.run(runner.tick(now=datetime(2026, 8, 30, 9, 15))) == []  # вс
    assert db.inserted == [] and enq == []


# ---------------- bounded: суточный потолок ----------------

def test_max_fires_per_day_guard():
    db, enq = FakeDB(), []
    runner = _runner(_agents(), db, enq, max_fires_per_day=2)
    for minute in (0, 15, 30, 45):
        asyncio.run(runner.tick(now=datetime(2026, 8, 31, 8, minute)))
    assert len(enq) == 2                               # потолок держит
    # новый день — счётчик обнуляется
    asyncio.run(runner.tick(now=datetime(2026, 9, 1, 8, 0)))
    assert len(enq) == 3


# ---------------- без перекрытий ----------------

def test_overlapping_fire_skipped_when_previous_task_running():
    db, enq = FakeDB(overlap=True), []
    runner = _runner(_agents(), db, enq)
    assert asyncio.run(runner.tick(now=datetime(2026, 8, 31, 9, 15))) == []
    assert db.inserted == [] and enq == []
    # предыдущая завершилась — следующая минута срабатывает
    db.overlap = False
    assert asyncio.run(runner.tick(now=datetime(2026, 8, 31, 9, 30))) == [1]


# ---------------- сбой не убивает петлю ----------------

def test_db_failure_once_is_counted_and_next_tick_fires():
    db, enq = FakeDB(), []
    db.raise_once = True
    runner = _runner(_agents(), db, enq)
    assert asyncio.run(runner.tick(now=datetime(2026, 8, 31, 9, 15))) == []
    assert runner.failures["fresh-vibes"] == 1
    assert asyncio.run(runner.tick(now=datetime(2026, 8, 31, 9, 30))) == [1]
    assert enq == [1]


def test_bad_cron_of_one_agent_does_not_break_others():
    db, enq = FakeDB(), []
    agents = {"broken": SimpleNamespace(schedule="каждые 15 минут"),
              "ok": SimpleNamespace(schedule=CRON)}
    runner = _runner(agents, db, enq)
    fired = asyncio.run(runner.tick(now=datetime(2026, 8, 31, 9, 15)))
    assert fired == [1] and runner.failures["broken"] == 1
    assert db.inserted[0][0] == "ok"


def test_loop_iteration_failure_does_not_kill_loop(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    runner = ScheduleRunner(lambda: {}, tick_interval=0)
    ticks = {"n": 0}

    async def flaky_tick(now=None):
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise RuntimeError("итерация упала")
        return []

    async def fake_sleep(_):
        if ticks["n"] >= 2:
            runner.stop()

    monkeypatch.setattr(runner, "tick", flaky_tick, raising=False)
    monkeypatch.setattr(schedule_runner.asyncio, "sleep", fake_sleep)
    asyncio.run(runner.loop())
    assert ticks["n"] >= 2                 # после сбоя петля продолжила тикать
    assert runner.loop_failures == 1
