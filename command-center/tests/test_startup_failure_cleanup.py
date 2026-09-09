"""A failed application startup must release resources acquired before failure."""
import asyncio

import pytest
import sqlalchemy as sa

from bcc.api import create_app
from bcc.features import Feature
from .conftest import make_settings


@pytest.mark.parametrize('cancelled', [False, True])
async def test_partial_startup_releases_owned_subscriptions_and_database(tmp_path, cancelled):
    app = create_app(make_settings(tmp_path), start_workers=False, announce_token=False)
    svc = app.state.svc
    entered = asyncio.Event()
    task = None
    pool = svc.db.engine.pool

    async def subscribed_worker():
        queue = svc.bus.subscribe()
        try:
            async with svc.db.session() as session:
                await session.execute(sa.text('SELECT 1'))
                entered.set()
                await queue.get()
        finally:
            svc.bus.unsubscribe(queue)

    async def fail_after_resource_acquisition(service):
        nonlocal task
        task = asyncio.create_task(subscribed_worker(), name='bcc-startup-owned')
        service._tasks.append(task)
        await asyncio.wait_for(entered.wait(), 5)
        assert pool.checkedout() == 1 and svc.bus._subscribers
        if cancelled:
            raise asyncio.CancelledError()
        raise RuntimeError('controlled setup failure')

    svc.features = [Feature(name='controlled-startup', setup=fail_after_resource_acquisition)]
    try:
        with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
            async with app.router.lifespan_context(app):
                pytest.fail('A failed startup cannot serve the application')
        assert task is not None and task.done()
        assert not svc.bus._subscribers
        assert pool.checkedout() == 0
        assert not svc.startup.ready
    finally:
        await svc.stop()
