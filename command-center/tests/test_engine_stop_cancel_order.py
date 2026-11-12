"""Architecture P0 step 4: Stop cancels the run's worker task LAST, after all its own DB work.

Root cause of the flaky test_stop_leaves_no_connection_behind (reproduced ~6 of 124 under parallel load, 0 of 96 after the
fix): a handler that stops its own run is awaited by that run, so cancelling the run first cancelled the handler in the
middle of `_stop_delegated_children` (DB queries) and stranded a pooled connection."""
from __future__ import annotations

import asyncio

import sqlalchemy as sa

from bcc.db import task_runs as runs_t

from .helpers import make_stack


async def test_worker_is_cancelled_only_after_delegated_children_are_handled(env):
    ids = await make_stack(env.client, max_steps=2)
    task_id = ids["task"]["id"]
    eng = env.svc.engine
    async with env.svc.db.session() as s:
        run_id = (await s.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == task_id))).scalars().first()
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(status="running"))
        await s.commit()
    worker = asyncio.create_task(asyncio.sleep(60), name="fake-run")
    eng._active[run_id] = worker
    seen = {}
    original = eng._stop_delegated_children

    async def spy(parent_id):
        seen["cancelling_during_children_work"] = worker.cancelling() > 0 or worker.cancelled()
        return await original(parent_id)

    eng._stop_delegated_children = spy
    try:
        res = await eng.stop(task_id)
        await asyncio.sleep(0)
        assert res["status"] == "stopped"
        assert seen == {"cancelling_during_children_work": False}, "worker cancelled before the DB work was done"
        await asyncio.gather(worker, return_exceptions=True)
        assert worker.cancelled(), "the other run's worker must still be cancelled by Stop"
    finally:
        eng._stop_delegated_children = original
        eng._active.pop(run_id, None)
        worker.cancel()
