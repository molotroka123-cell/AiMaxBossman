"""Real SQLite connections across the unchanged Services STOP_GRACE boundary.

These tests retain the original pool after dispose, register the tested loop for
soft shutdown, and distinguish cooperative exit from forced cancellation. Slow
session cancellation and cancellation during a real SQLite query are separate
cases. Host-specific driver timing remains a Windows owner acceptance boundary.
"""
from __future__ import annotations

import asyncio
import threading

import sqlalchemy as sa

from .conftest import client_for, make_settings, start_app
from .helpers import make_stack


def _own_tasks() -> set[asyncio.Task]:
    return {task for task in asyncio.all_tasks() if not task.done()
            and task.get_name().startswith(("bcc-", "feature-", "worker"))}


class SlowDbLoop:
    def __init__(self, svc, hold_seconds):
        self.svc = svc
        self.hold_seconds = hold_seconds
        self.entered = asyncio.Event()
        self.cancelled = False
        self.finished = False

    async def run(self):
        try:
            while not self.svc._stopping.is_set():
                async with self.svc.db.session() as session:
                    await session.execute(sa.text("SELECT 1"))
                    self.entered.set()
                    await asyncio.sleep(self.hold_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.finished = True


async def _with_slow_loop(tmp_path, hold_seconds):
    _, svc = await start_app(make_settings(tmp_path), start_workers=False)
    loop = SlowDbLoop(svc, hold_seconds)
    task = asyncio.create_task(loop.run(), name="bcc-slow-db-loop")
    svc._tasks.append(task)
    svc._graceful.add(task)
    await asyncio.wait_for(loop.entered.wait(), timeout=10)
    pool = svc.db.engine.pool
    assert pool.checkedout() >= 1, "negative control: loop holds a real connection"
    return svc, loop, task, pool


async def test_a_loop_slower_than_stop_grace_still_stops_the_service(tmp_path):
    svc, loop, task, pool = await _with_slow_loop(tmp_path, hold_seconds=5.0)
    assert svc.STOP_GRACE < 5.0
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    elapsed = asyncio.get_running_loop().time() - started
    assert task.done() and loop.finished and loop.cancelled
    assert svc.STOP_GRACE <= elapsed < svc.STOP_TIMEOUT + 10
    assert pool.checkedout() == 0
    assert not _own_tasks()


async def test_no_connection_is_left_checked_out_after_a_cancelled_db_call(tmp_path):
    _, svc = await start_app(make_settings(tmp_path), start_workers=False)
    entered = threading.Event()
    released = threading.Event()
    def blocking_query():
        entered.set()
        # Bounded driver-side work: cancellation lands inside SQLite execute,
        # not in a later asyncio.sleep while merely holding a session.
        released.wait(3.0)
        return 1
    async with svc.db.engine.connect() as conn:
        await conn.run_sync(lambda sync: sync.connection.dbapi_connection.create_function(
            "owner_slow_query", 0, blocking_query))
    async def query():
        async with svc.db.session() as session:
            await session.execute(sa.text("SELECT owner_slow_query()"))
    task = asyncio.create_task(query(), name="bcc-slow-sql-query")
    svc._tasks.append(task)
    svc._graceful.add(task)
    assert await asyncio.to_thread(entered.wait, 5)
    pool = svc.db.engine.pool
    assert pool.checkedout() >= 1
    try:
        await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 10)
        assert task.cancelled(), "query must exceed the grace window"
        assert pool.checkedout() == 0, "check the original pool, not its empty replacement"
        assert not _own_tasks()
    finally:
        released.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await svc.stop()


async def test_a_second_stop_is_idempotent(tmp_path):
    svc, loop, task, pool = await _with_slow_loop(tmp_path, hold_seconds=5.0)
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    assert task.done() and pool.checkedout() == 0


async def test_a_fast_loop_leaves_within_the_grace_window(tmp_path):
    svc, loop, task, pool = await _with_slow_loop(tmp_path, hold_seconds=0.05)
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < svc.STOP_GRACE
    assert loop.finished and not loop.cancelled and not task.cancelled()
    assert pool.checkedout() == 0


async def test_start_stop_start_works_and_resurrects_nothing(tmp_path):
    from bcc.db import task_runs as runs_t
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    async with client_for(app, svc) as client:
        ids = await make_stack(client, max_steps=1)
        task_id = ids["task"]["id"]
    pool = svc.db.engine.pool
    await svc.stop()
    assert pool.checkedout() == 0
    app2, svc2 = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with svc2.db.session() as session:
            rows = [dict(row._mapping) for row in (await session.execute(
                sa.select(runs_t).where(runs_t.c.task_id == task_id))).all()]
        assert len(rows) == 1 and rows[0]["status"] == "queued"
        assert await svc2.engine.claim() == rows[0]["id"], "unstarted work must remain usable"
        assert svc2.db.engine.pool.checkedout() == 0
    finally:
        await svc2.stop()


async def test_feature_tick_loops_are_drained_too(tmp_path):
    _, svc = await start_app(make_settings(tmp_path), start_workers=True)
    pool = svc.db.engine.pool
    try:
        await asyncio.sleep(0.1)
        tasks = list(svc._tasks)
        assert any(task.get_name().startswith("bcc-") for task in tasks)
    finally:
        await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 20)
    assert pool.checkedout() == 0
    assert all(task.done() for task in tasks)
    assert not _own_tasks()


async def test_owned_subscriptions_are_released_by_stop(tmp_path):
    _, svc = await start_app(make_settings(tmp_path), start_workers=True)
    pool = svc.db.engine.pool
    await asyncio.sleep(0.05)
    assert svc.bus._subscribers, "negative control: service has live subscriptions"
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 20)
    assert not svc.bus._subscribers
    assert pool.checkedout() == 0


async def test_stopped_queued_run_is_not_claimable_after_restart(tmp_path):
    """Actual precondition is queued+stopped; post-effect crash has its own suite."""
    from bcc.db import task_runs as runs_t
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    async with client_for(app, svc) as client:
        ids = await make_stack(client, max_steps=1)
        task_id = ids["task"]["id"]
        assert (await client.post(f"/api/tasks/{task_id}/stop")).status_code == 200
    async with svc.db.session() as session:
        before = [dict(row._mapping) for row in (await session.execute(
            sa.select(runs_t).where(runs_t.c.task_id == task_id))).all()]
    assert len(before) == 1 and before[0]["status"] == "stopped"
    await svc.stop()
    app2, svc2 = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        assert await svc2.engine.claim() is None
        async with svc2.db.session() as session:
            after = [dict(row._mapping) for row in (await session.execute(
                sa.select(runs_t).where(runs_t.c.task_id == task_id))).all()]
        assert after == before
    finally:
        await svc2.stop()


async def test_repeated_start_stop_does_not_accumulate_subscriptions(tmp_path):
    _, svc = await start_app(make_settings(tmp_path), start_workers=True)
    counts = []
    for round_id in range(3):
        if round_id:
            await svc.start()
        await asyncio.sleep(0.05)
        counts.append(len(svc.bus._subscribers))
        pool = svc.db.engine.pool
        await svc.stop()
        assert not svc.bus._subscribers
        assert pool.checkedout() == 0
        assert not _own_tasks()
    assert counts[0] > 0 and counts == [counts[0]] * 3
