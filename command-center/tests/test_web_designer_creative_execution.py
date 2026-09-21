"""Production HTTP router + real files. Model replies are explicit test doubles.

Not Windows/GUI/live-model evidence. Test data stays in the test's tmp_path.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from bcc.features import web_designer as wd
from bcc import web_designer_creative_brief as brief
from bcc.provider_governance import GovernedAdapter
from bcc.providers import ChatResult, ProviderError

HTML = '<!doctype html><html><head><title>Fixture</title></head><body><h1 id="hero">Creative fixture</h1></body></html>'


def output(**patch):
    return json.dumps({"html": HTML, "direction": "Clear hierarchy", "sections": ["hero"],
        "critique": "Browser testing remains required", "refinements": ["Readable heading"], **patch})


class Reply:
    def __init__(self):
        self.calls = []
        self.text = output()
        self.finish = "stop"
        self.callback = None
        self.error = None

    async def chat(self, model, messages, **kwargs):
        self.calls.append((model, messages, kwargs))
        if self.callback:
            await self.callback()
        if self.error:
            raise self.error
        return ChatResult(text=self.text, finish=self.finish)


class Registry:
    def __init__(self):
        self.reply = Reply()
        self.models = [{"id": 7, "name": "fixture-local", "kind": "local", "provider_id": 3}]
        self.providers = [{"id": 3, "kind": "openai_compat", "base_url": "http://127.0.0.1:1234/v1"}]
        self.adapter_calls = 0
        self.changed_provider = None

    async def list_models(self):
        return self.models

    async def list_providers(self):
        return self.providers

    async def adapter_for(self, mid):
        self.adapter_calls += 1
        model = next(m for m in self.models if m["id"] == mid)
        provider = self.changed_provider or self.providers[0]
        return GovernedAdapter(self.reply, provider, model), model


@pytest.fixture
async def creative_http(tmp_path):
    registry = Registry()
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), registry=registry)
    app = FastAPI()
    app.state.svc = svc
    app.include_router(wd.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, registry, svc


async def test_catalog_wires_ai_into_existing_create_and_generate_selectors(creative_http):
    client, _, _ = creative_http
    result = (await client.get('/api/web-designer/templates')).json()
    choices = [t for t in result['items'] if t['id'] == 'ai_local']
    assert len(choices) == 1 and 'ИИ' in choices[0]['title']


async def test_create_ai_invokes_existing_brief_once_and_saves_exact_html(creative_http, monkeypatch):
    client, registry, svc = creative_http
    seen = []
    original = brief.render_default_creative_brief
    def spy(**kwargs):
        seen.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(brief, 'render_default_creative_brief', spy)
    result = await client.post('/api/web-designer/projects', json={
        'name': 'Fixture owner', 'prompt': 'One clear landing page', 'template': 'ai_local'})
    assert result.status_code == 200, result.text
    site = result.json()
    assert site['code'] == HTML
    assert len(seen) == len(registry.reply.calls) == 1
    assert seen[0]['brand'] == 'Fixture owner'
    messages = registry.reply.calls[0][1]
    assert all(s['title'] in messages[1]['content'] for s in brief.STAGES)
    proof = site['meta']['creative_build']
    assert proof['model_calls'] == 1 and proof['route'] == 'LOCAL_ONLY'
    assert proof['html_sha256'] == hashlib.sha256(HTML.encode()).hexdigest()
    assert proof['browser_quality_gates'] == 'NOT_RUN'
    pid = site['meta']['id']
    assert (wd._pdir(svc, pid) / 'history/v1.html').read_text() == HTML


async def test_create_ai_without_model_does_not_create_template_or_empty_project(creative_http):
    client, registry, svc = creative_http
    registry.models = []
    response = await client.post('/api/web-designer/projects', json={'name': 'No model', 'template': 'ai_local'})
    assert response.status_code == 409 and 'NOT CONFIGURED' in response.text
    assert not (svc.settings.data_dir / 'web_designer').exists()
    assert not registry.reply.calls


async def test_regenerate_ai_uses_brief_and_preserves_edit_preview_restart_rollback(creative_http):
    client, registry, svc = creative_http
    initial = (await client.post('/api/web-designer/projects', json={'name': 'Start', 'template': 'blank'})).json()
    pid = initial['meta']['id']
    url = f'/api/web-designer/projects/{pid}'
    ai = await client.post(url + '/generate', json={'template': 'ai_local', 'prompt': 'Build', 'base_version': 1})
    assert ai.status_code == 200, ai.text
    assert len(registry.reply.calls) == 1 and ai.json()['steps'] == [HTML]
    preview = await client.get(url + '/preview')
    assert preview.status_code == 200 and 'Creative fixture' in preview.text
    assert 'sandbox allow-scripts' in preview.headers['content-security-policy']
    edited = HTML.replace('Creative fixture', 'Owner edit retained')
    assert (await client.put(url+'/code', json={'html': edited, 'base_version': 2})).status_code == 200
    assert (await client.get(url)).json()['code'] == edited
    script = '''import sys; from pathlib import Path; from types import SimpleNamespace; from bcc.features import web_designer as w
s=SimpleNamespace(settings=SimpleNamespace(data_dir=Path(sys.argv[1])))
p,m=w._require_project(s,int(sys.argv[2])); assert w._read_code(p,m)==sys.argv[3]; print('NEW_PROCESS_READ=PASS')'''
    read = subprocess.run([sys.executable, '-c', script, str(svc.settings.data_dir), str(pid), edited],
        text=True, capture_output=True, timeout=20, check=True)
    assert 'NEW_PROCESS_READ=PASS' in read.stdout
    restored = await client.post(url + '/versions/2/restore')
    assert restored.status_code == 200 and restored.json()['code'] == HTML
    assert (await client.get(url)).json()['code'] == HTML


@pytest.mark.parametrize('provider', [
    {'id':3,'kind':'openrouter','base_url':'https://openrouter.ai/api/v1'},
    {'id':3,'kind':'openai_compat','base_url':'https://example.com/v1'},
    {'id':3,'kind':'openai_compat','base_url':''},
])
async def test_falsely_local_provider_is_refused_before_adapter(creative_http, provider):
    client, registry, _ = creative_http
    registry.providers = [provider]
    response = await client.post('/api/web-designer/projects', json={'name':'Private', 'template':'ai_local'})
    assert response.status_code == 409
    assert registry.adapter_calls == 0 and registry.reply.calls == []


async def test_route_changed_during_selection_cannot_egress(creative_http):
    client, registry, _ = creative_http
    registry.changed_provider = {'id':3,'kind':'openai_compat','base_url':'https://example.com/v1'}
    response = await client.post('/api/web-designer/projects', json={'name':'Private', 'template':'ai_local'})
    assert response.status_code == 409 and 'LOCAL_ONLY' in response.text
    assert registry.reply.calls == []


@pytest.mark.parametrize('text', ['not json', '{}', '[]', output(html='<div>fragment</div>'),
    output(html=HTML.replace('</html>', '')), output(sections='not list'), output(direction=''),
    output(extra='model claims PASS'), output(html=''), output(critique=True),
    output(html=HTML+'\\u0000').replace('\\\\u0000','\\u0000'),
    output(html=HTML+'\ud800'), output().replace('{','{\"html\":\"duplicate\",',1)])
async def test_malformed_ai_response_never_replaces_a_saved_version(creative_http, text):
    client, registry, _ = creative_http
    initial = (await client.post('/api/web-designer/projects', json={'name':'Existing','template':'blank'})).json()
    url = f"/api/web-designer/projects/{initial['meta']['id']}"
    registry.reply.text = text
    response = await client.post(url + '/generate', json={'template':'ai_local','base_version':1})
    assert response.status_code == 502
    after = (await client.get(url)).json()
    assert after['code'] == initial['code'] and after['meta']['version'] == 1


@pytest.mark.parametrize('status', ['offline', 'error'])
async def test_known_unavailable_model_is_not_blindly_retried(creative_http, status):
    client, registry, _ = creative_http
    registry.models[0]['status'] = status
    response = await client.post('/api/web-designer/projects', json={'name':'Fail','template':'ai_local'})
    assert response.status_code == 409 and not registry.reply.calls


async def test_provider_failure_is_sanitized_and_not_retried(creative_http):
    client, registry, _ = creative_http
    registry.reply.error = ProviderError('secret-value-must-not-leak', kind='http')
    response = await client.post('/api/web-designer/projects', json={'name':'Fail','template':'ai_local'})
    assert response.status_code == 502 and 'secret-value' not in response.text
    assert len(registry.reply.calls) == 1


async def test_timeout_cancels_call_without_fallback(creative_http, monkeypatch):
    client, registry, svc = creative_http
    monkeypatch.setattr(wd.creative, 'AI_BUILD_TIMEOUT', .01)
    cancelled = asyncio.Event()
    async def stall():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    registry.reply.callback = stall
    response = await client.post('/api/web-designer/projects', json={'name':'Fail','template':'ai_local'})
    assert response.status_code == 504 and cancelled.is_set()
    assert len(registry.reply.calls) == 1 and not (svc.settings.data_dir / 'web_designer').exists()


async def test_stale_revision_refused_before_model_and_midflight_change_preserved(creative_http):
    client, registry, _ = creative_http
    site = (await client.post('/api/web-designer/projects', json={'name':'Race','template':'blank'})).json()
    url = f"/api/web-designer/projects/{site['meta']['id']}"
    response = await client.post(url+'/generate',json={'template':'ai_local','base_version':0})
    assert response.status_code == 409 and not registry.reply.calls
    changed = HTML.replace('Creative fixture', 'Concurrent edit')
    async def save_during_model():
        assert (await client.put(url+'/code',json={'html':changed,'base_version':1})).status_code == 200
    registry.reply.callback = save_during_model
    response = await client.post(url+'/generate',json={'template':'ai_local','base_version':1})
    assert response.status_code == 409 and (await client.get(url)).json()['code'] == changed
    assert len(registry.reply.calls) == 1


async def test_deterministic_template_mode_still_works_without_a_model(creative_http):
    client, registry, _ = creative_http
    registry.models = []
    response = await client.post('/api/web-designer/projects',json={'name':'Local template','template':'landing'})
    assert response.status_code == 200 and '<html' in response.json()['code']
    assert not registry.reply.calls
