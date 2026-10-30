"""Long-horizon run 30.10: a task resumed after an approval must reuse its own memory reservation, not stack another one."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import resource_reservations as res_t
from bcc.features import resources as res

from .conftest import make_settings, start_app


async def _held(svc, task_id):
    async with svc.db.session() as s:
        return [tuple(r) for r in (await s.execute(sa.select(res_t.c.id, res_t.c.status).where(
            res_t.c.holder_kind == "task", res_t.c.holder_id == task_id))).fetchall()]


@pytest.mark.anyio
async def test_own_reservation_is_reused_and_duplicates_released(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        a = await res._reserve(svc, "task", 798, 16000)
        b = await res._reserve(svc, "task", 798, 16000)           # the stacked copy the bug produced
        assert await res._renew_own(svc, 798, run_id=1) is True
        rows = dict(await _held(svc, 798))
        assert rows[b] == "held" and rows[a] == "released"       # newest kept, the extra one released
        assert await res._renew_own(svc, 798, run_id=1) is True
        assert sum(st == "held" for st in (await _held(svc, 798)) and dict(await _held(svc, 798)).values()) == 1
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_a_task_without_its_own_reservation_is_not_waved_through(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        await res._reserve(svc, "task", 1, 16000)                 # someone else's reservation
        assert await res._renew_own(svc, 2, run_id=1) is False   # task 2 must pass the normal admission
        snap = await res._snapshot(svc, {"reserve_floor_mb": 16000, "policy": "balanced"})
        assert snap.reserved_mb == 16000                         # other holders still count
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_a_stopped_tasks_reservation_is_released_by_the_tick(tmp_path):
    from bcc.db import tasks as tasks_t
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with svc.db.session() as s:
            res1 = await s.execute(sa.insert(tasks_t).values(prompt="x", title="t", status="stopped"))
            res2 = await s.execute(sa.insert(tasks_t).values(prompt="y", title="u", status="running"))
            stopped, running = int(res1.inserted_primary_key[0]), int(res2.inserted_primary_key[0])
            await s.commit()
        await res._reserve(svc, "task", stopped, 16000)
        await res._reserve(svc, "task", stopped, 16000)
        await res._reserve(svc, "task", running, 16000)
        await res._tick(svc)
        assert all(st != "held" for _, st in await _held(svc, stopped))      # both copies freed
        assert [st for _, st in await _held(svc, running)] == ["held"]       # a live task keeps its memory
    finally:
        await svc.stop()
