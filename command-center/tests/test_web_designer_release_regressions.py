"""Release regressions: concurrent generation and honest failed deletion."""
from __future__ import annotations


async def test_generation_refuses_a_stale_owner_version(env):
    created = (await env.client.post('/api/web-designer/projects',
               json={'name': 'Concurrent owner', 'template': 'blank'})).json()
    pid = created['meta']['id']
    version = created['meta']['version']
    code = '<html><body><h1>KEEP OTHER TAB</h1></body></html>'
    saved = await env.client.put(f'/api/web-designer/projects/{pid}/code',
                                json={'html': code, 'base_version': version})
    assert saved.status_code == 200
    result = await env.client.post(f'/api/web-designer/projects/{pid}/generate',
                                  json={'prompt': 'new site', 'base_version': version})
    assert result.status_code == 409
    actual = (await env.client.get(f'/api/web-designer/projects/{pid}')).json()
    assert actual['code'] == code
    assert actual['meta']['version'] == version + 1


async def test_generation_on_the_current_version_still_works_once(env):
    created = (await env.client.post('/api/web-designer/projects',
               json={'name': 'Owner', 'template': 'blank'})).json()
    pid = created['meta']['id']
    version = created['meta']['version']
    body = {'prompt': 'new site', 'base_version': version}
    first = await env.client.post(f'/api/web-designer/projects/{pid}/generate', json=body)
    assert first.status_code == 200
    assert first.json()['meta']['version'] == version + 1
    repeat = await env.client.post(f'/api/web-designer/projects/{pid}/generate', json=body)
    assert repeat.status_code == 409
    actual = (await env.client.get(f'/api/web-designer/projects/{pid}')).json()
    assert actual['meta']['version'] == version + 1
    assert actual['code'] == first.json()['steps'][-1]


async def test_a_locked_project_is_not_reported_as_deleted(env, monkeypatch):
    from bcc.features import web_designer

    created = (await env.client.post('/api/web-designer/projects',
               json={'name': 'Locked owner project', 'template': 'blank'})).json()
    pid = created['meta']['id']
    project_dir = web_designer._pdir(env.svc, pid)
    real_rmtree = web_designer.shutil.rmtree

    def locked(path, *args, **kwargs):
        if path == project_dir:
            if kwargs.get('ignore_errors'):
                return
            raise PermissionError('owner file locked')
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(web_designer.shutil, 'rmtree', locked)
    response = await env.client.delete(f'/api/web-designer/projects/{pid}')
    assert response.status_code == 409
    assert 'не удалён' in response.json()['error']['message']
    assert project_dir.exists()
    actual = (await env.client.get(f'/api/web-designer/projects/{pid}')).json()
    assert actual['code'] == created['code']
    monkeypatch.setattr(web_designer.shutil, 'rmtree', real_rmtree)
    removed = await env.client.delete(f'/api/web-designer/projects/{pid}')
    assert removed.status_code == 200 and removed.json()['ok'] is True
    assert not project_dir.exists()
