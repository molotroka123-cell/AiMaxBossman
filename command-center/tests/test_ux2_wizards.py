"""Bounded source-UI wizard contracts against a fresh, guarded local backend.

This is not a full controls sweep. No model/provider/agent/mission/schedule is
created. Only an explicitly synthetic objective may be persisted as DRAFT.
The API allowlist, fresh environment and child audit hook precede app imports.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import chromium_path, reason as browser_reason

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.timeout(180)
NOT_RUN = [
    "model/provider save, health/discovery and external inference",
    "positive agent, mission, schedule or task creation/execution",
    "objective enrollment, activation, lifecycle changes or owner Apply/pin",
    "live profiles, Telegram, app start/stop, paid or destructive controls",
    "other routes, populated/edit variants, complete wizard/control inventory",
    "real Windows ACL subprocess and git-backed runtime provenance (fixture replacements)",
]

# Executed only in our identified subprocess. No pytest/conftest imports in it.
SERVE = r'''
import os, sys, json, socket, traceback
from pathlib import Path
root, folder, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
folder = folder.resolve()
keys = ('LOCALAPPDATA','APPDATA','USERPROFILE','HOME','TEMP','TMP','BCC_DATA_DIR',
        'BCC_APPS_DIR','BOSSMAN_V5_STORE','BOSSMAN_TELEGRAM_CONFIG','BOSSMAN_COMPANION_CONFIG')
assert all(Path(os.environ[k]).resolve().is_relative_to(folder) for k in keys)
assert not any(name == 'bcc' or name.startswith('bcc.') for name in sys.modules)
proof = {'before_backend_imports': True, 'paths': {k:os.environ[k] for k in keys}}
attempts, requests, own_sockets = [], [], []
def audit(event, args):
    if event == 'socket.bind': own_sockets.append(args[0])
    if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1','::1','localhost',None):
        attempts.append('DNS'); raise PermissionError('WIZARD_OUTBOUND_DENIED')
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and address[0] in ('127.0.0.1','::1'):
            for sock in own_sockets:
                try:
                    if sock.getsockname()[1] == address[1]: return
                except OSError: pass
        attempts.append('CONNECT'); raise PermissionError('WIZARD_OUTBOUND_DENIED')
    if event in {'socket.sendto','subprocess.Popen','os.system','os.exec','os.posix_spawn','os.spawn'}:
        frames = [{'module':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                  for frame in traceback.extract_stack(limit=18)[:-1]]
        with (folder/'guard-callsite.jsonl').open('a',encoding='utf-8') as receipt:
            receipt.write(json.dumps({'event':event,'frames':frames})+'\n')
        attempts.append(event); raise PermissionError('WIZARD_EXECUTION_DENIED')
sys.addaudithook(audit)
# A blocked synthetic connection proves the hook is active, without packets.
try:
    with socket.socket() as probe: probe.connect(('127.0.0.1',1))
except PermissionError: pass
else: raise AssertionError('OUTBOUND_GUARD_INACTIVE')
assert attempts == ['CONNECT']
proof['outbound_guard_probe'] = 'BLOCKED_BEFORE_CONNECT'
attempts.clear()
sys.path[:0] = [str(root/'command-center'),str(root/'bossman-core'),str(root)]
os.chdir(folder)
from bcc.v2 import skill_library
skill_root = folder/'skills'; skill_root.mkdir()
skill_library.default_skill_roots = lambda ignored: [skill_root]
from bcc import auth, run_provenance
def fixture_acl(path):
    assert Path(path).resolve().is_relative_to(folder), 'ACL_TARGET_OUTSIDE_FIXTURE'
auth._restrict_to_owner = fixture_acl
run_provenance.repository_sha = lambda *a, **kw: run_provenance.NOT_CAPTURED
proof['fixture_replacements'] = ['fresh-path-only ACL noop', 'repository_sha=NOT_CAPTURED']
from bcc.api import create_app
from bcc.config import Settings
from bossman_shared import fable_budget
fable_budget.LEDGER_PATH = folder/'data'/'fixture-budget.json'
app = create_app(Settings(data_dir=folder/'data', database_url='sqlite+aiosqlite:///'+str(folder/'data'/'bcc.db'),
                          ui_dir=root/'command-center'/'ui'), start_workers=False, announce_token=False)
app.state.svc.skills = skill_library.SkillLibrary([skill_root], skill_root)
proof['skill_roots'] = [str(p) for p in app.state.svc.skills.roots]
proof['workers'] = app.state.svc.start_workers
(folder/'isolation-proof.json').write_text(json.dumps(proof),encoding='utf-8')
(folder/'fixture-login').write_text(app.state.svc.auth.token,encoding='utf-8')
from starlette.responses import JSONResponse
get_allowed = {'/api/login-hint','/api/models','/api/providers','/api/providers/kinds',
               '/api/missions','/api/schedules','/api/objectives','/api/objectives/status','/api/approvals',
               '/api/testing/status','/api/command-bar',
               '/api/evolution/status'}  # read-only poll: the capability tree opens once per campaign
fixtures = {
    '/api/system': {'cpu_percent':0,'ram_total':1,'ram_used':0,'gpus':[]},
    '/api/identity': {'source_sha':'fixture','source_dirty':True},
    '/api/agents': [{'id':7001,'name':'Synthetic disabled agent','enabled':False,'model_id':None}],
}
class Guard:
    def __init__(self, inner): self.inner=inner
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            if scope['path'] != '/api/events':
                attempts.append('UNEXPECTED_WEBSOCKET')
                await send({'type':'websocket.close','code':1008}); return
            return await self.inner(scope,receive,send)
        if scope['type'] != 'http': return await self.inner(scope,receive,send)
        method, path = scope['method'],scope['path']
        if path == '/api/__ux_guard' and method == 'GET':
            return await JSONResponse({'attempts':attempts,'requests':requests,'proof':proof})(scope,receive,send)
        if path.startswith('/api/'):
            requests.append({'method':method,'path':path})
            if method == 'GET' and path in fixtures:
                return await JSONResponse(fixtures[path])(scope,receive,send)
            allowed = method == 'GET' and (path in get_allowed or path == '/api/objectives/ux-wizard-synthetic')
            allowed |= method == 'POST' and path in {'/api/login','/api/objectives/preview','/api/objectives'}
            if allowed and method == 'POST' and path == '/api/objectives':
                from starlette.requests import Request
                request = Request(scope, receive)
                body = await request.body()
                spec = json.loads(body).get('spec',{})
                allowed = (spec.get('objective_id') == 'ux-wizard-synthetic' and spec.get('scope_id') == 'ux-fixture'
                    and spec.get('permission_refs') == [] and spec.get('limits',{}).get('max_cost_usd') == 0)
                async def replay(): return {'type':'http.request','body':body,'more_body':False}
                receive = replay
            if not allowed:
                attempts.append('UNEXPECTED_API:'+method+':'+path)
                return await JSONResponse({'detail':'WIZARD_ALLOWLIST_DENIED'},status_code=409)(scope,receive,send)
        elif method not in {'GET','HEAD'}:
            attempts.append('UNEXPECTED_STATIC_MUTATION')
            return await JSONResponse({},status_code=409)(scope,receive,send)
        return await self.inner(scope,receive,send)
import uvicorn
uvicorn.run(Guard(app),host='127.0.0.1',port=port,log_level='warning',access_log=False,timeout_graceful_shutdown=1)
'''


def _isolated_env(folder):
    folder.mkdir(parents=True, exist_ok=False)
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT') if key in os.environ}
    paths = {"LOCALAPPDATA": "local", "APPDATA": "roaming", "USERPROFILE": "home", "HOME": "home",
             "TEMP": "temp", "TMP": "temp", "BCC_DATA_DIR": "data", "BCC_APPS_DIR": "apps"}
    for key, relative in paths.items():
        path = folder / relative
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    env.update(BOSSMAN_V5_STORE=str(folder / 'data' / 'objectives.sqlite3'),
               BOSSMAN_TELEGRAM_CONFIG=str(folder / 'data' / 'telegram-companion' / 'config.json'),
               BOSSMAN_COMPANION_CONFIG=str(folder / 'data' / 'telegram-companion' / 'config.json'),
               BOSSMAN_CALLS_HOME=str(folder / 'data' / 'calls'),
               BOSSMAN_TELEGRAM_POLLER_LOCK_FILE=str(folder / 'data' / 'poller.lock'),
               PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return env


class GuardedServer:
    def __init__(self, folder):
        self.folder = folder
        env = _isolated_env(folder)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.log = (folder / 'server.log').open('x', encoding='utf-8')
        self.process = subprocess.Popen([sys.executable, '-I', '-B', '-c', SERVE, str(ROOT), str(folder), str(self.port)],
                                        env=env, cwd=folder, stdout=self.log, stderr=subprocess.STDOUT)
        (folder / 'process.json').write_text(json.dumps({'pid': self.process.pid, 'role': 'isolated wizard fixture',
            'port': self.port, 'folder': str(folder)}), encoding='utf-8')

    def get(self, path):
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(self.url + path, headers={'X-BCC-Token': (self.folder / 'fixture-login').read_text()})
            assert response.status_code == 200, (path, response.status_code)
            return response.json()

    def wait(self):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert self.process.poll() is None, 'Guarded fixture exited; inspect synthetic server.log'
            if (self.folder / 'fixture-login').exists():
                try:
                    self.get('/api/__ux_guard')
                    return self
                except httpx.HTTPError:
                    pass
            time.sleep(.05)
        raise AssertionError('Guarded fixture startup timeout')

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()  # Only the subprocess created and retained above.
            self.process.wait(timeout=15)
        self.log.close()
        (self.folder / 'process-finished.json').write_text(json.dumps({'pid': self.process.pid,
            'exit_code': self.process.returncode, 'active': False}), encoding='utf-8')


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    srv = GuardedServer(tmp_path_factory.mktemp('wizard-guard') / 'server')
    try:
        yield srv.wait()
    finally:
        srv.stop()


@pytest.fixture
def ui(server, tmp_path):
    executable = chromium_path()
    assert executable, browser_reason()  # Required coverage cannot silently skip.
    before = server.get('/api/__ux_guard')
    assert before['attempts'] == [], before['attempts']
    assert before['proof']['before_backend_imports'] is True
    assert before['proof']['outbound_guard_probe'] == 'BLOCKED_BEFORE_CONNECT'
    assert before['proof']['workers'] is False
    assert all(Path(p).is_relative_to(server.folder) for p in before['proof']['paths'].values())
    assert all(Path(p).is_relative_to(server.folder) for p in before['proof']['skill_roots'])
    browser_attempts, errors = [], []
    # Driver and Chromium get a fresh allowlisted environment, including temp/profile.
    clean_env = _isolated_env(tmp_path / 'browser-env')
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(clean_env)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=executable, env=clean_env,
                args=['--disable-background-networking', '--disable-quic', '--disable-component-update',
                      '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
            def route(request_route):
                if urlsplit(request_route.request.url).scheme in {'http','https'} and request_route.request.url.startswith(server.url + '/'):
                    request_route.continue_()
                else:
                    browser_attempts.append('OUTSIDE_ORIGIN_REQUEST')
                    request_route.abort()
            context.route('**/*', route)
            def websocket(ws):
                if ws.url == server.url.replace('http:', 'ws:') + '/api/events':
                    ws.connect_to_server()
                else:
                    browser_attempts.append('OUTSIDE_ORIGIN_WEBSOCKET')
                    ws.close()
            context.route_web_socket('**/*', websocket)
            def observe(page):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('framenavigated', lambda frame: browser_attempts.append('OUTSIDE_ORIGIN_NAVIGATION')
                        if frame.url != 'about:blank' and not frame.url.startswith(server.url + '/') else None)
            context.on('page', observe)
            page = context.new_page()
            page.goto(server.url + '/#/models', wait_until='domcontentloaded')
            page.locator('#login-token').fill((server.folder / 'fixture-login').read_text())
            page.locator('#login-submit').click()
            expect(page.locator('#shell')).to_be_visible()
            yield page
            browser.close()
    finally:
        os.environ.clear()
        os.environ.update(original)
        after = server.get('/api/__ux_guard')
        outcome = {'backend_attempts': after['attempts'], 'browser_attempts': browser_attempts,
                   'page_errors': errors, 'requests': after['requests'][len(before['requests']):],
                   'not_run': NOT_RUN, 'fixture_stubs': ['/api/system', '/api/identity', '/api/agents']}
        (tmp_path / 'wizard-evidence.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
        assert not after['attempts'], after['attempts']
        assert not browser_attempts, browser_attempts
        assert not errors, errors


def open_form(page, server, route, label):
    page.goto(server.url + '/#/' + route, wait_until='domcontentloaded')
    page.locator('#view').get_by_role('button', name=label, exact=True).first.click()
    modal = page.locator('#modal-root .modal')
    expect(modal).to_be_visible()
    return modal


def warning(page, text):
    expect(page.locator('#toast-root .toast-warn .toast-msg').filter(has_text=re.compile('^' + re.escape(text) + '$'))).to_be_visible()


def objective_field(modal, label):
    return modal.get_by_text(label, exact=True).locator('..').locator('input')


def mutations(server):
    return [r for r in server.get('/api/__ux_guard')['requests'] if r['method'] != 'GET' and r['path'] != '/api/login']


def test_model_wizard_validation_back_cancel(ui, server):
    before = mutations(server)
    modal = open_form(ui, server, 'models', 'Добавить модель')
    modal.get_by_role('button', name='Далее', exact=True).click()
    warning(ui, 'Укажите название провайдера')
    expect(modal.get_by_placeholder('Local llama.cpp')).to_have_value('')
    modal.get_by_placeholder('Local llama.cpp').fill('Synthetic offline provider')
    modal.get_by_role('button', name='Далее', exact=True).click()
    warning(ui, 'Укажите Base URL')
    modal.get_by_placeholder('http://127.0.0.1:8080/v1').fill('http://127.0.0.1:1/v1')
    modal.get_by_role('button', name='Далее', exact=True).click()
    modal.get_by_role('button', name='Сохранить', exact=True).click()
    warning(ui, 'Укажите имя модели')
    modal.get_by_placeholder('qwen2.5-coder:14b').fill('synthetic-model')
    modal.get_by_placeholder('qwen-coder').fill('')
    modal.get_by_role('button', name='Сохранить', exact=True).click()
    warning(ui, 'Укажите alias')
    modal.get_by_role('button', name='Назад', exact=True).click()
    expect(modal.get_by_placeholder('Local llama.cpp')).to_have_value('Synthetic offline provider')
    modal.get_by_role('button', name='Далее', exact=True).click()
    expect(modal.get_by_placeholder('qwen2.5-coder:14b')).to_have_value('synthetic-model')
    modal.get_by_role('button', name='Отмена', exact=True).click()
    expect(modal).to_have_count(0)
    modal = open_form(ui, server, 'models', 'Добавить модель')
    expect(modal.get_by_placeholder('Local llama.cpp')).to_have_value('')
    modal.get_by_role('button', name='Отмена', exact=True).click()
    assert mutations(server) == before


@pytest.mark.parametrize('route,opener,submit,message,field_name', [
    ('agents', 'Новый агент', 'Создать', 'Укажите имя агента', 'Имя'),
    ('missions', 'Новая миссия', 'Создать', 'Укажите название миссии', 'Название'),
])
def test_empty_forms_validate_and_cancel(ui, server, route, opener, submit, message, field_name):
    before = mutations(server)
    modal = open_form(ui, server, route, opener)
    modal.get_by_role('button', name=submit, exact=True).click()
    warning(ui, message)
    expect(modal.get_by_label(field_name, exact=True)).to_be_focused()
    modal.get_by_label(field_name, exact=True).fill('Synthetic cancelled value')
    if route == 'agents':
        modal.get_by_role('button', name=submit, exact=True).click()
        warning(ui, 'Выберите основную модель')
    modal.get_by_role('button', name='Отмена', exact=True).click()
    expect(modal).to_have_count(0)
    modal = open_form(ui, server, route, opener)
    expect(modal.get_by_label(field_name, exact=True)).to_have_value('')
    ui.keyboard.press('Escape')
    expect(modal).to_have_count(0)
    assert mutations(server) == before


@pytest.mark.parametrize('mode,label,value,message', [
    ('Разово', 'Дата и время', '', 'Укажите дату и время'),
    ('Ежедневно', 'Время запуска', '', 'Укажите время'),
    ('Интервал', 'Интервал, минут', '0', 'Интервал — минимум 1 минута'),
])
def test_schedule_modes_reject_invalid_values_without_creation(ui, server, mode, label, value, message):
    before = mutations(server)
    modal = open_form(ui, server, 'schedules', 'Новое расписание')
    modal.get_by_role('button', name='Создать', exact=True).click()
    warning(ui, 'Укажите название')
    modal.get_by_label('Название', exact=True).fill('Synthetic schedule')
    modal.get_by_role('button', name='Создать', exact=True).click()
    warning(ui, 'Опишите задачу')
    modal.get_by_label('Задача', exact=True).fill('Synthetic never executed')
    modal.get_by_label('Агент', exact=True).select_option('7001')
    modal.get_by_role('button', name=mode, exact=True).click()
    modal.get_by_label(label, exact=True).fill(value)
    modal.get_by_role('button', name='Создать', exact=True).click()
    warning(ui, message)
    expect(modal.get_by_label(label, exact=True)).to_have_value(value)
    modal.get_by_role('button', name='Отмена', exact=True).click()
    expect(modal).to_have_count(0)
    assert mutations(server) == before


def test_objective_negative_preview_preserves_draft_and_cancel(ui, server):
    before = server.get('/api/objectives')
    modal = open_form(ui, server, 'objectives', 'Новая цель')
    objective_field(modal, 'Идентификатор цели').fill('ux-wizard-invalid')
    objective_field(modal, 'Источник').fill('synthetic-source')
    modal.get_by_role('button', name='Далее', exact=True).click()
    objective_field(modal, 'Область').fill('ux-fixture')
    modal.get_by_role('button', name='Назад', exact=True).click()
    expect(objective_field(modal, 'Источник')).to_have_value('synthetic-source')
    modal.get_by_role('button', name='Далее', exact=True).click()
    expect(objective_field(modal, 'Область')).to_have_value('ux-fixture')
    modal.get_by_role('button', name='Далее', exact=True).click()
    objective_field(modal, 'Потолок времени, с').fill('-1')
    with ui.expect_response(lambda r: r.url.endswith('/api/objectives/preview') and r.request.method == 'POST') as response:
        modal.get_by_role('button', name='Далее', exact=True).click()
    assert response.value.status == 400
    assert response.value.json()['error']['message'] == 'max_wall_seconds: finite nonnegative number required'
    expect(objective_field(modal, 'Потолок времени, с')).to_have_value('-1')
    expect(modal.get_by_role('button', name='Создать цель', exact=True)).to_have_count(0)
    assert server.get('/api/objectives') == before
    modal.get_by_role('button', name='Отмена', exact=True).click()
    modal = open_form(ui, server, 'objectives', 'Новая цель')
    expect(objective_field(modal, 'Идентификатор цели')).to_have_value('')
    modal.get_by_role('button', name='Отмена', exact=True).click()


def test_objective_positive_persists_only_inert_local_draft(ui, server):
    assert server.get('/api/objectives') == []
    modal = open_form(ui, server, 'objectives', 'Новая цель')
    objective_field(modal, 'Идентификатор цели').fill('ux-wizard-synthetic')
    objective_field(modal, 'Источник').fill('synthetic-source')
    modal.get_by_role('button', name='Далее', exact=True).click()
    objective_field(modal, 'Область').fill('ux-fixture')
    modal.get_by_role('button', name='Далее', exact=True).click()
    objective_field(modal, 'Потолок расхода, $').fill('0')
    with ui.expect_response(lambda r: r.url.endswith('/api/objectives/preview') and r.request.method == 'POST') as preview:
        modal.get_by_role('button', name='Далее', exact=True).click()
    assert preview.value.status == 200, preview.value.json()
    assert preview.value.json()['created'] is False
    assert server.get('/api/objectives') == []
    expect(modal.get_by_text('нет — только наблюдение', exact=True)).to_be_visible()
    modal.get_by_role('button', name='Назад', exact=True).click()
    expect(objective_field(modal, 'Потолок расхода, $')).to_have_value('0')
    modal.get_by_role('button', name='Далее', exact=True).click()
    with ui.expect_response(lambda r: r.url.endswith('/api/objectives') and r.request.method == 'POST') as created:
        modal.get_by_role('button', name='Создать цель', exact=True).click()
    assert created.value.status == 200
    expect(modal).to_have_count(0)
    ui.reload(wait_until='domcontentloaded')
    expect(ui.locator('#view').get_by_role('heading', name='ux-wizard-synthetic', exact=True)).to_be_visible()
    record = server.get('/api/objectives/ux-wizard-synthetic')
    assert record['lifecycle'] == 'DRAFT'
    assert record['condition'] == 'UNKNOWN'
    assert record['enrolled_sources'] == []
    assert record['missions_used'] == record['observations_used'] == 0
    assert record['cost_usd_used'] == 0
    assert record['spec']['permission_refs'] == []
    assert record['spec']['limits']['max_cost_usd'] == 0
    status = server.get('/api/objectives/status')
    assert status['standing_autonomy_enabled'] is False
    assert status['observers_running'] is status['admission_enabled'] is False
    assert status['counts']['total'] == 1 and status['counts']['active'] == 0
