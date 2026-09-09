"""MF-032: liveness is not readiness; stale measurements cannot read green."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from bcc import db as dbm, model_health
from .helpers import make_stack


async def ready_components(env):
    stack = await make_stack(env.client)
    now = datetime.now(timezone.utc)
    record = model_health.HealthRecord(status=model_health.HEALTHY, checked_at=now,
                                       last_success_at=now, successes=1, samples=1)
    async with env.svc.db.session() as session:
        await session.execute(sa.update(dbm.models).where(dbm.models.c.id == stack['model']['id'])
                              .values(health=record.to_dict(), status='online'))
        await session.commit()
    env.settings.ui_dir.mkdir(parents=True)
    (env.settings.ui_dir / 'index.html').write_text('<html></html>', encoding='utf-8')
    env.svc.start_workers = True
    env.svc.engine.last_tick = env.svc.scheduler.last_tick = env.svc.metrics.last_tick = time.monotonic()
    for state in env.svc.feature_ticks.values():
        state.update(at=time.monotonic(), error=None)
    return stack


async def test_fresh_install_alive_but_not_configured(env):
    # No authentication is needed for public status, and no private detail leaks.
    env.client.headers.pop('X-BCC-Token')
    live = await env.client.get('/health/live')
    assert live.status_code == 200 and live.json()['alive'] is True
    for path in ('/health', '/healthz'):
        response = await env.client.get(path)
        assert response.status_code == 503
        result = response.json()
        assert result['alive'] is True and result['ready'] is False
        assert result['components']['models']['status'] == 'NOT_CONFIGURED'
        assert result['components']['providers']['status'] == 'NOT_CONFIGURED'
        assert result['components']['queue_worker']['status'] == 'STOPPED'
        assert all(set(entry) == {'status'} for entry in result['components'].values())
    assert (await env.client.get('/api/health')).status_code == 401


async def test_unknown_provider_is_never_healthy(env):
    await make_stack(env.client)
    system = (await env.client.get('/api/system')).json()
    assert system['health']['models']['status'] == 'unknown'
    result = (await env.client.get('/api/health')).json()
    assert result['components']['providers']['status'] == 'UNKNOWN'
    assert result['ready'] is False


async def test_imported_browser_adapter_without_live_context_is_not_health_proof(env, monkeypatch):
    class InstalledAdapter:
        available = True
        _sessions = {}
    monkeypatch.setattr(env.svc, 'browser', InstalledAdapter())
    response = await env.client.get('/health')
    assert response.json()['components']['browser']['status'] == 'UNKNOWN'


async def test_only_measured_ready_components_report_ready(env):
    await ready_components(env)
    response = await env.client.get('/health')
    assert response.status_code == 200, response.text
    assert response.json()['ready'] is True
    assert response.json()['components']['models']['status'] == 'HEALTHY'


@pytest.mark.parametrize('fault', ['worker', 'scheduler', 'feature', 'model', 'db', 'stopping'])
async def test_ready_snapshot_goes_red_on_real_component_fault(env, monkeypatch, fault):
    stack = await ready_components(env)
    if fault == 'worker':
        env.svc.engine.last_tick = time.monotonic() - 60
    elif fault == 'scheduler':
        env.svc.scheduler.last_tick = time.monotonic() - 3600
    elif fault == 'feature':
        env.svc.feature_ticks['review_gate']['error'] = 'OperationalError: failed'
    elif fault == 'model':
        old = datetime.now(timezone.utc) - timedelta(days=1)
        record = model_health.HealthRecord(status=model_health.HEALTHY, checked_at=old)
        async with env.svc.db.session() as session:
            await session.execute(sa.update(dbm.models).where(dbm.models.c.id == stack['model']['id'])
                                  .values(health=record.to_dict()))
            await session.commit()
    elif fault == 'db':
        async def failed_ping():
            raise RuntimeError('private path and password must not escape public health')
        monkeypatch.setattr(env.svc.db, 'ping', failed_ping)
    else:
        env.svc._stopping.set()
    response = await env.client.get('/health')
    assert response.status_code == 503
    assert response.json()['ready'] is False
    assert response.json()['status'] == 'UNHEALTHY'
    assert 'password' not in response.text


async def test_completed_worker_is_unhealthy_even_with_fresh_heartbeat(env):
    await ready_components(env)
    task = asyncio.create_task(asyncio.sleep(0), name='bcc-worker')
    await task
    env.svc._tasks.append(task)
    response = await env.client.get('/health')
    assert response.status_code == 503
    assert response.json()['components']['queue_worker']['status'] == 'UNHEALTHY'


@pytest.mark.parametrize('name', ['scheduler', 'metrics', 'queue_worker'])
async def test_loop_failure_is_visible_while_process_remains_alive(env, monkeypatch, name):
    await ready_components(env)
    component = env.svc.engine if name == 'queue_worker' else getattr(env.svc, name)
    called = asyncio.Event()

    async def failure():
        called.set()
        if name != 'queue_worker':
            env.svc._stopping.set()
        raise RuntimeError('loop negative control')

    async def recovered():
        return None

    operation = {'scheduler': 'tick_once', 'metrics': 'sample', 'queue_worker': 'claim'}[name]
    monkeypatch.setattr(component, operation, failure)
    if name == 'queue_worker':
        monkeypatch.setattr(component, 'recover', recovered)
    task = asyncio.create_task(component.worker_loop() if name == 'queue_worker' else component.loop())
    try:
        await asyncio.wait_for(called.wait(), timeout=2)
        assert component.last_error and 'negative control' in component.last_error
        env.svc._stopping.clear()
        response = await env.client.get('/health')
        assert response.status_code == 503
        assert response.json()['components'][name]['status'] == 'UNHEALTHY'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
