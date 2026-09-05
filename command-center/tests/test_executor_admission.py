"""Real API/SQLite admission; providers are deterministic and never use the network."""
import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t
from .conftest import FakeAdapter
from .helpers import make_stack


async def test_owner_command_without_agent_is_blocked_before_run(env):
    response = await env.client.post('/api/tasks', json={
        'title': 'Доложи состояние миссии', 'prompt': 'Доложи состояние миссии',
        'run_now': True,
    })
    assert response.status_code == 200
    task = response.json()['task']
    assert task['status'] == 'blocked'
    assert task['meta']['reason_code'] == 'BLOCKED_CAPABILITY_UNAVAILABLE'
    data = (await env.client.get(f"/api/tasks/{task['id']}")).json()
    assert data['runs'] == []
    assert data['error']
    assert await env.svc.engine.claim() is None


@pytest.mark.parametrize('action', ['run', 'retry', 'resume'])
async def test_manual_admission_never_claims_queued_without_executor(env, action):
    task = (await env.client.post('/api/tasks', json={
        'prompt': 'Read-only report', 'run_now': False,
    })).json()['task']
    result = (await env.client.post(f"/api/tasks/{task['id']}/{action}")).json()
    assert result['ok'] is False
    assert result['status'] == 'blocked'
    assert result['code'] == 'BLOCKED_CAPABILITY_UNAVAILABLE'
    assert result['run_id'] is None
    assert (await env.client.get(f"/api/tasks/{task['id']}")).json()['runs'] == []


async def test_disabled_executor_blocks_then_reenabled_executor_runs(env):
    fake = FakeAdapter('4')
    env.svc.registry.adapter_factory = lambda m, p: fake
    ids = await make_stack(env.client)
    task_id, agent_id = ids['task']['id'], ids['agent']['id']
    await env.client.post(f'/api/tasks/{task_id}/stop')
    await env.client.patch(f'/api/agents/{agent_id}', json={'enabled': False})
    result = (await env.client.post(f'/api/tasks/{task_id}/retry')).json()
    assert result['status'] == 'blocked'
    assert len((await env.client.get(f'/api/tasks/{task_id}')).json()['runs']) == 1
    await env.client.patch(f'/api/agents/{agent_id}', json={'enabled': True})
    result = (await env.client.post(f'/api/tasks/{task_id}/retry')).json()
    assert result['status'] == 'queued'
    await env.svc.engine.execute(await env.svc.engine.claim())
    data = (await env.client.get(f'/api/tasks/{task_id}')).json()
    assert data['task']['status'] == 'completed'
    assert 'reason_code' not in data['task']['meta']
    assert fake.calls == 1


async def test_executor_removed_after_enqueue_is_blocked_without_model_call(env):
    fake = FakeAdapter('must not execute')
    env.svc.registry.adapter_factory = lambda m, p: fake
    ids = await make_stack(env.client)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == ids['task']['id']).values(agent_id=None))
        await s.commit()
    run_id = await env.svc.engine.claim()
    await env.svc.engine.execute(run_id)
    data = (await env.client.get(f"/api/tasks/{ids['task']['id']}")).json()
    assert data['task']['status'] == 'blocked'
    assert data['runs'][-1]['status'] == 'blocked'
    assert data['runs'][-1]['started_at'] is None
    assert fake.calls == 0
