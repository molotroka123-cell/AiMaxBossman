"""Real Web Designer router + filesystem checks; not full-product/UI attestation."""
from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bcc.features.web_designer import router

OWNER_HTML = '<!DOCTYPE html>\n<html><head><title>Owner</title></head><body><h1>KEEP</h1><p>REMOVE ME</p></body></html>'


@pytest.fixture
def owner(tmp_path: Path):
    app = FastAPI()
    app.state.svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    app.include_router(router, prefix='/api')
    with TestClient(app) as client:
        created = client.post('/api/web-designer/projects', json={'name': 'Owner root safety', 'template': 'blank'})
        assert created.status_code == 200
        pid = created.json()['meta']['id']
        url = f'/api/web-designer/projects/{pid}'
        saved = client.put(url + '/code', json={'html': OWNER_HTML})
        assert saved.status_code == 200
        version = saved.json()['meta']['version']
        preview = client.get(url + '/preview')
        assert preview.status_code == 200
        ids = {}
        for tag in ('html', 'body', 'p'):
            opening = re.search(rf'<{tag}\b[^>]*>', preview.text)
            assert opening is not None
            found = re.search(r'data-bd-id="(bd-\d+)"', opening.group(0))
            assert found is not None
            ids[tag] = found.group(1)
        yield client, url, version, ids, tmp_path / 'web_designer' / str(pid)


def disk_state(pdir: Path) -> dict[str, bytes]:
    return {p.relative_to(pdir).as_posix(): p.read_bytes() for p in pdir.rglob('*') if p.is_file()}


def edit_target(tag: str, selector: str, ids: dict[str, str]) -> dict:
    if selector == 'path':
        return {'path': tag}
    if selector == 'conflicting_path':
        return {'bd_id': ids[tag], 'path': 'p'}
    if selector == 'spoofed_tag':
        return {'bd_id': ids[tag], 'tag': 'p'}
    return {'bd_id': ids[tag]}


@pytest.mark.parametrize('tag', ['html', 'body'])
@pytest.mark.parametrize('selector', ['bd_id', 'path', 'conflicting_path', 'spoofed_tag'])
@pytest.mark.parametrize('ack', [None, False])
def test_root_delete_without_explicit_approval_is_refused_and_preserves_all_files(owner, tag, selector, ack):
    client, url, version, ids, pdir = owner
    before = disk_state(pdir)
    payload = {'op': 'delete', 'base_version': version, **edit_target(tag, selector, ids)}
    if ack is not None:
        payload['destructive_root_ack'] = ack
    response = client.post(url + '/edit', json=payload)
    assert response.status_code == 409, response.text
    assert 'ENTIRE SITE/DOCUMENT' in response.json()['detail']
    assert disk_state(pdir) == before
    reopened = client.get(url).json()
    assert reopened['code'] == OWNER_HTML
    assert reopened['meta']['version'] == version


@pytest.mark.parametrize('tag', ['html', 'body'])
@pytest.mark.parametrize('revision', [None, -1])
def test_root_delete_ack_without_current_revision_is_refused(owner, tag, revision):
    client, url, version, ids, pdir = owner
    before = disk_state(pdir)
    payload = {'op': 'delete', 'bd_id': ids[tag], 'destructive_root_ack': True}
    if revision is not None:
        payload['base_version'] = version + revision
    response = client.post(url + '/edit', json=payload)
    assert response.status_code == 409, response.text
    assert disk_state(pdir) == before


@pytest.mark.parametrize('tag', ['html', 'body'])
def test_approved_current_root_delete_remains_recoverable_and_cannot_repeat(owner, tag):
    client, url, version, ids, pdir = owner
    saved = (pdir / 'history' / f'v{version}.html').read_bytes()
    payload = {'op': 'delete', 'bd_id': ids[tag], 'tag': tag,
               'base_version': version, 'destructive_root_ack': True}
    response = client.post(url + '/edit', json=payload)
    assert response.status_code == 200, response.text
    assert 'KEEP' not in client.get(url).json()['code']
    assert (pdir / 'history' / f'v{version}.html').read_bytes() == saved
    after = disk_state(pdir)
    assert client.post(url + '/edit', json=payload).status_code == 409
    assert disk_state(pdir) == after
    restored = client.post(url + f'/versions/{version}/restore')
    assert restored.status_code == 200, restored.text
    assert client.get(url).json()['code'] == OWNER_HTML
    assert (pdir / 'current.html').read_text(encoding='utf-8') == OWNER_HTML


def test_ordinary_element_delete_keeps_working_without_root_ack(owner):
    client, url, version, ids, pdir = owner
    response = client.post(url + '/edit', json={'op': 'delete', 'bd_id': ids['p'], 'tag': 'p', 'base_version': version})
    assert response.status_code == 200, response.text
    code = client.get(url).json()['code']
    assert 'REMOVE ME' not in code and 'KEEP' in code and '</html>' in code
    assert (pdir / 'history' / f'v{version}.html').read_text(encoding='utf-8') == OWNER_HTML
