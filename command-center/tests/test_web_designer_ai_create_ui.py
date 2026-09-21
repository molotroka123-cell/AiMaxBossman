"""Actual creation/edit/restart/rollback UI with an explicit local HTTP model stub.

The browser and installed product are real; the generative model is NOT real.
No credentials, external service or owner files. Reuse the existing required
browser capability gate; this module is also part of windows-installed.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

from playwright.sync_api import expect, sync_playwright

from .test_web_designer_first_click_ui import live, pytestmark  # noqa: F401
from .test_editors_user_acceptance import editor_server, login  # noqa: F401
from .test_ux2_thinking_pane import _launch

HTML = ('<!doctype html><html><head><meta charset="utf-8"><title>Creative UI fixture</title>'
        '<style>body{font-family:sans-serif;padding:32px}h1{color:#205040}</style>'
        '</head><body><h1 id="creative-ui">Creative UI original</h1><p>Test data only.</p></body></html>')


def _expected_restart_console_error(text: str) -> bool:
    """Only transport noise caused by the deliberate server-death window.

    Killing the exact process under test necessarily races Chromium's resource
    loader and events WebSocket. Windows can surface the same socket teardown
    as either REFUSED or RESET depending on which side closes first. Hiding
    arbitrary console errors would weaken the acceptance gate, so the exemption
    is deliberately tiny and is applied only to entries captured between
    ``live.restart()`` and verified recovery.
    """
    transport = ('ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET')
    return (
        text.startswith('Failed to load resource: net::')
        and any(code in text for code in transport)
        or ('WebSocket connection to ' in text and '/api/events' in text
            and any(code in text for code in transport))
    )


def _expected_restart_request_failure(row: dict, base_url: str) -> bool:
    failure = row.get('failure') or ''
    return (
        row.get('url', '').startswith(base_url)
        and any(code in failure for code in (
            'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET', 'ERR_ABORTED'))
    )


def test_local_creative_creation_uses_real_ui_and_survives_edit_restart_rollback(live, tmp_path):
    calls = []
    release_response = threading.Event()
    envelope = json.dumps({'html': HTML, 'direction': 'Clear hierarchy', 'sections': ['hero'],
        'critique': 'This is a model stub, not an independent quality result',
        'refinements': ['Readable spacing']})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data, status=200):
            body = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({'object': 'list', 'data': [{'id': 'creative-ui-stub', 'object': 'model'}]})

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.reply({'error': 'unsupported fixture route'}, 404)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 64 * 1024:
                self.reply({'error': 'fixture body limit'}, 413)
                return
            calls.append(json.loads(self.rfile.read(size)))
            release_response.wait(5)
            self.reply({'model': 'creative-ui-stub', 'choices': [{
                'message': {'role': 'assistant', 'content': envelope}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proof_dir = Path(os.environ.get('BOSSMAN_EDITOR_EVIDENCE_DIR') or tmp_path / 'proof')
    proof_dir.mkdir(parents=True, exist_ok=True)
    page_errors, console_errors, request_failures, bad_http = [], [], [], []
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda err: page_errors.append(str(err)))
                page.on('console', lambda msg: console_errors.append(msg.text) if msg.type == 'error' else None)
                page.on('requestfailed', lambda req: request_failures.append({'method': req.method,
                    'url': req.url, 'failure': req.failure}))
                page.on('response', lambda res: bad_http.append((res.status, res.url))
                        if res.status >= 400 and res.url.startswith(live.url + '/api/') else None)
                login(page, live)
                # Configure only a disposable loopback model; production registry/governance is not mocked.
                page.evaluate('''async base => {
                    const headers={'Content-Type':'application/json','X-BCC-CSRF':localStorage.getItem('bcc.csrf')};
                    const post=async(path,body)=>{const r=await fetch(path,{method:'POST',headers,body:JSON.stringify(body)});
                        if(!r.ok)throw new Error(await r.text());return r.json();};
                    const p=await post('/api/providers',{name:'Creative UI local stub',kind:'openai_compat',base_url:base});
                    await post('/api/models',{provider_id:p.id,name:'creative-ui-stub',alias:'creative-ui-stub',kind:'local'});
                }''', f'http://127.0.0.1:{server.server_port}/v1')
                page.goto(live.url + '/#/web_designer')
                page.locator('input[placeholder^="Название проекта"]').fill('Creative UI acceptance')
                page.locator('textarea[placeholder^="О чём сайт"]').fill('Create a self-contained test website.')
                page.locator('button.bd-tpl').filter(has_text='ИИ: творческий бриф (локально)').click()
                button = page.get_by_role('button', name='Открыть проект', exact=True)
                button.dblclick()
                expect(button).to_be_disabled()
                release_response.set()
                page.locator('iframe.bd-frame').wait_for(timeout=20000)
                expect(page.frame_locator('iframe.bd-frame').locator('#creative-ui')).to_have_text('Creative UI original')
                assert len(calls) == 1, 'double click must not cause two model requests'
                assert 'PREMIUM STUDIO BASELINE' in calls[0]['messages'][1]['content']
                assert calls[0]['model'] == 'creative-ui-stub'
                projects = page.request.get(live.url + '/api/web-designer/projects').json()['items']
                assert len(projects) == 1
                pid = projects[0]['id']
                endpoint = live.url + f'/api/web-designer/projects/{pid}'
                initial = page.request.get(endpoint).json()
                assert initial['code'] == HTML
                assert initial['meta']['creative_build']['browser_quality_gates'] == 'NOT_RUN'
                assert initial['meta']['creative_build']['html_sha256'] == hashlib.sha256(HTML.encode()).hexdigest()
                edited = HTML.replace('Creative UI original', 'Creative UI edited')
                editor = page.locator('textarea.bd-code')
                editor.fill(edited)
                editor.press('Control+s')
                page.wait_for_function('''async ([url, wanted]) => {
                    const r=await fetch(url);return r.ok && (await r.json()).code===wanted;
                }''', arg=[endpoint, edited], timeout=15000)
                page.reload()
                expect(page.locator('textarea.bd-code')).to_have_value(edited)

                # Record the deliberate outage separately. Chromium is allowed
                # to report only connection-refused/reset noise while the exact
                # test server is dead; recovery is then proved through UI + API.
                restart_console_at = len(console_errors)
                restart_requests_at = len(request_failures)
                live.restart()
                page.reload()
                expect(page.frame_locator('iframe.bd-frame').locator('#creative-ui')).to_have_text('Creative UI edited')
                assert page.request.get(endpoint).json()['code'] == edited
                restart_console = console_errors[restart_console_at:]
                restart_requests = request_failures[restart_requests_at:]
                assert all(_expected_restart_console_error(line) for line in restart_console), restart_console
                assert all(_expected_restart_request_failure(row, live.url) for row in restart_requests), restart_requests
                del console_errors[restart_console_at:]
                del request_failures[restart_requests_at:]

                first_version = page.locator('div.bd-vers').filter(has=page.locator('b', has_text='v1'))
                first_version.get_by_role('button', name='Вернуть', exact=True).click()
                page.get_by_role('dialog').get_by_role('button', name='Вернуть', exact=True).click()
                expect(page.locator('textarea.bd-code')).to_have_value(HTML)
                assert page.request.get(endpoint).json()['code'] == HTML
                page.screenshot(path=str(proof_dir / 'creative-ui-restored.png'), full_page=True)
                assert not page_errors, page_errors
                assert not console_errors, console_errors
                assert not bad_http, bad_http
                unexpected = [r for r in request_failures if 'ERR_ABORTED' not in (r['failure'] or '')]
                assert not unexpected, unexpected
                (proof_dir / 'creative-ui.json').write_text(json.dumps({
                    'source_sha': os.environ.get('BCC_ACCEPTANCE_SOURCE_SHA'),
                    'archive_sha256': os.environ.get('BOSSMAN_ARCHIVE_SHA256'),
                    'harness_sha': os.environ.get('BOSSMAN_HARNESS_SHA'),
                    'real_browser': True, 'model': 'EXPLICIT_LOOPBACK_HTTP_STUB', 'live_model': False,
                    'model_requests': len(calls), 'project_count': len(projects),
                    'html_sha256': hashlib.sha256(HTML.encode()).hexdigest(),
                    'create_edit_reload_restart_rollback': 'PASS', 'request_failures': request_failures,
                }, indent=2), encoding='utf-8')
            finally:
                browser.close()
    finally:
        release_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), 'local fixture server did not stop'
