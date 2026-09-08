"""Final narrow breaker: real HTTP/SQLite boundaries with controlled failures."""
import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.conftest"]
from tests.helpers import make_stack
from tests.test_apps_control import apps_root, make_app
from bcc.db import task_runs, tasks


@pytest.mark.parametrize("value", ["nan", "-1024"])
async def test_invalid_memory_requirement_not_admitted(env, value):
    r = await env.client.get('/api/reality/strategies', params={'large_model_mb': value})
    body = r.json()
    ranked = [x['strategy_id'] for x in body.get('ranked', [])]
    print(f"requirement={value} http={r.status_code} ranked={ranked} memory={body.get('memory')}")
    assert r.status_code >= 400 or 'large-model-tools' not in ranked


@pytest.mark.parametrize("repeat", [1, 2])
async def test_alternating_failure_classes_eventually_stop(env, repeat):
    stack = await make_stack(env.client, max_retries=0)
    task = stack['task']
    run_id = await env.svc.engine.claim()
    states = []
    for index in range(12):
        error = 'unsupported tool use' if index % 2 == 0 else 'empty response no content'
        await env.svc.engine._handle_failure(run_id, task, error, [], 0)
        async with env.svc.db.session() as s:
            row = (await s.execute(sa.select(task_runs).where(task_runs.c.id == run_id))).mappings().one()
        states.append((row['status'], row['attempt'], row['checkpoint'].get('recovery_ladder')))
        if row['status'] == 'failed':
            break
    print(f"repeat={repeat} max_retries=0 transitions={states}")
    assert states[-1][0] == 'failed', 'alternating classes replenish spent degraded rung'


async def test_apps_repeated_enable_start_stop_disable(env, apps_root, monkeypatch):
    from bcc.features import apps_control as ctl
    monkeypatch.delenv(ctl.FLAG, raising=False)
    monkeypatch.delenv(ctl.LOCK_ENV, raising=False)
    make_app(apps_root, 'breaker-app')
    for _ in range(2):
        for enabled in [True, True]:
            assert (await env.client.put('/api/apps/control/policy', json={'enabled':enabled})).status_code == 200
        first = (await env.client.post('/api/apps/breaker-app/start')).json()
        second = (await env.client.post('/api/apps/breaker-app/start')).json()
        assert first['started'] and second['already_running'] and not second['started']
        for _ in range(2):
            assert (await env.client.post('/api/apps/breaker-app/stop')).status_code == 200
        for _ in range(2):
            assert (await env.client.put('/api/apps/control/policy', json={'enabled':False})).status_code == 200
        assert (await env.client.post('/api/apps/breaker-app/start')).status_code >= 400
    print('two full enable/start/start/stop/stop/disable/disable/refused-start cycles passed')
