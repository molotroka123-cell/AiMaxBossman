"""MF-031: real HTTP/SQLite preflight; no model-backed live proof is claimed."""
import pytest

from .helpers import make_stack


async def submit(env, **kw):
    return await env.client.post('/api/tasks', json={
        'prompt': 'Посчитай 17*23. Не используй инструменты.', 'run_now': True, **kw})


async def count(env):
    return len((await env.client.get('/api/tasks')).json())


async def test_auto_selects_configured_agent_and_preserves_policy(env):
    stack = await make_stack(env.client)
    before = (await env.client.get('/api/agents')).json()
    response = await submit(env)
    assert response.status_code == 200, response.text
    task = response.json()['task']
    assert task['agent_id'] == stack['agent']['id']
    assert task['status'] == 'queued'
    assert (await env.client.get('/api/agents')).json() == before


async def test_explicit_agent_is_not_silently_replaced(env):
    stack = await make_stack(env.client)
    other = (await env.client.post('/api/agents', json={
        'name': 'Выбранный', 'model_id': stack['model']['id']})).json()
    response = await submit(env, agent_id=other['id'])
    assert response.status_code == 200, response.text
    assert response.json()['task']['agent_id'] == other['id']


@pytest.mark.parametrize('explicit', [False, True])
async def test_disabled_agent_refused_without_task(env, explicit):
    stack = await make_stack(env.client)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={'enabled': False})
    before = await count(env)
    response = await submit(env, **({'agent_id': stack['agent']['id']} if explicit else {}))
    assert response.status_code == 409
    assert 'выключен' in response.json()['error']['message']
    assert await count(env) == before


@pytest.mark.parametrize('explicit', [False, True])
async def test_policy_denied_agent_refused_without_task(env, explicit):
    stack = await make_stack(env.client)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={
        'permissions': {'tool_rules': [{'tool': 'terminal.*', 'resource': '*', 'effect': 'deny'}]}})
    before = await count(env)
    response = await submit(env, prompt='Запусти команду в терминале',
                            **({'agent_id': stack['agent']['id']} if explicit else {}))
    assert response.status_code == 409
    assert 'Политика' in response.json()['error']['message']
    assert await count(env) == before


async def test_auto_selection_skips_denied_candidate(env):
    stack = await make_stack(env.client)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={
        'permissions': {'tool_rules': [{'tool': '*', 'resource': '*', 'effect': 'deny'}]}})
    capable = (await env.client.post('/api/agents', json={
        'name': 'С подтверждением', 'model_id': stack['model']['id'], 'tools': ['terminal.run']})).json()
    response = await submit(env, prompt='Запусти команду в терминале')
    assert response.status_code == 200, response.text
    assert response.json()['task']['agent_id'] == capable['id']
    # ASK is not denial and has not been turned into an implicit approval.
    assert (await env.client.get('/api/approvals')).json() == []


@pytest.mark.parametrize('stale', ['agent', 'model'])
async def test_stale_owner_selection_refused_without_task(env, stale):
    stack = await make_stack(env.client)
    await env.client.delete(f"/api/{stale}s/{stack[stale]['id']}")
    before = await count(env)
    response = await submit(env, agent_id=stack['agent']['id'])
    assert response.status_code == 409, response.text
    assert await count(env) == before


async def test_no_capable_agent_is_refused_instead_of_false_queue(env):
    await make_stack(env.client)
    before = await count(env)
    response = await submit(env, prompt='Сгенерируй изображение лисы')
    assert response.status_code == 409, response.text
    assert 'IMAGES_ACTION' in response.json()['error']['message']
    assert await count(env) == before


async def test_conversation_needs_model_but_does_not_need_action_tools(env):
    await make_stack(env.client)
    response = await submit(env)
    assert response.status_code == 200, response.text
    assert response.json()['task']['status'] == 'queued'


async def test_intentional_non_execution_draft_remains_available(env):
    response = await submit(env, run_now=False)
    assert response.status_code == 200
    assert response.json()['task']['status'] == 'draft'
    assert response.json()['task']['agent_id'] is None
    assert await env.svc.engine.claim() is None


async def test_schedule_persists_selected_executor_in_actual_template(env):
    stack = await make_stack(env.client)
    response = await submit(env, run_now=False, schedule={
        'name': 'Позже', 'kind': 'interval', 'interval_minutes': 60,
        'task_template': {'prompt': 'Посчитай 2+2', 'agent_id': None}})
    assert response.status_code == 200, response.text
    assert response.json()['schedule']['task_template']['agent_id'] == stack['agent']['id']


async def test_schedule_cannot_hide_an_unusable_executor_in_template(env):
    stack = await make_stack(env.client)
    before = await count(env)
    response = await submit(env, agent_id=stack['agent']['id'], schedule={
        'name': 'Не создавать', 'kind': 'interval', 'interval_minutes': 60,
        'task_template': {'agent_id': 999999, 'prompt': 'Посчитай 2+2'}})
    assert response.status_code == 409
    assert await count(env) == before
    assert (await env.client.get('/api/schedules')).json() == []
