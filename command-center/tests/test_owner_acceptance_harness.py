"""Harness safety/integrity; these tests never substitute for a live model run."""
import contextlib
import json
import os
import sqlite3

from cryptography.fernet import Fernet
import pytest

from bcc import owner_acceptance as owner
from bcc.secrets import KEY_FILE
from .helpers import make_stack


async def test_private_registry_does_not_copy_owner_jobs_or_vault_identity(env, tmp_path):
    stack = await make_stack(env.client)
    await env.client.post('/api/schedules', json={
        'name': 'Must not run', 'kind': 'interval', 'interval_minutes': 1,
        'task_template': {'prompt': 'Never copy me', 'agent_id': stack['agent']['id']}})
    before_tasks = (await env.client.get('/api/tasks')).json()
    original_key = (env.settings.data_dir / KEY_FILE).read_bytes()
    selected = owner.configured_model(env.settings.data_dir, None)
    assert selected['secret'] == 'sk-test-abcd'
    private = tmp_path / 'isolated'
    private.mkdir(mode=0o700)
    await owner.seed_private_registry(private, selected)
    new_key = (private / KEY_FILE).read_bytes()
    assert new_key != original_key
    with sqlite3.connect(private / 'bcc.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM schedules').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM providers').fetchone()[0] == 1
        encrypted = conn.execute('SELECT api_key_enc FROM providers').fetchone()[0]
        assert Fernet(new_key).decrypt(encrypted.encode()).decode() == 'sk-test-abcd'
        assert conn.execute('SELECT fallback_model_id FROM agents').fetchone()[0] is None
        assert conn.execute('SELECT tools FROM agents').fetchone()[0] == '[]'
    assert (env.settings.data_dir / KEY_FILE).read_bytes() == original_key
    assert (await env.client.get('/api/tasks')).json() == before_tasks
    if os.name != 'nt':
        assert (private / KEY_FILE).stat().st_mode & 0o777 == 0o600


async def test_harness_only_copies_selected_provider_credential(env, tmp_path):
    first = await make_stack(env.client)
    provider = (await env.client.post('/api/providers', json={
        'name': 'Not selected', 'kind': 'openai_compat', 'api_key': 'other-secret'})).json()
    model = (await env.client.post('/api/models', json={
        'provider_id': provider['id'], 'name': 'other-model'})).json()
    await env.client.post('/api/agents', json={'name': 'Other', 'model_id': model['id']})
    selected = owner.configured_model(env.settings.data_dir, first['agent']['id'])
    assert selected['provider']['id'] == first['provider']['id']
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    await owner.seed_private_registry(private, selected)
    assert b'other-secret' not in (private / 'bcc.db').read_bytes()
    with sqlite3.connect(private / 'bcc.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM providers').fetchone()[0] == 1


async def test_missing_model_is_external_not_a_fabricated_pass(env):
    with pytest.raises(owner.OwnerRequired, match='No enabled agent'):
        owner.configured_model(env.settings.data_dir, None)


async def test_broken_vault_is_owner_required_and_never_exposes_secret(env, monkeypatch):
    await make_stack(env.client)
    monkeypatch.setenv('BOSSMAN_VAULT_KEY', Fernet.generate_key().decode())
    with pytest.raises(owner.OwnerRequired) as exc:
        owner.configured_model(env.settings.data_dir, None)
    assert 'sk-test' not in str(exc.value)


@pytest.mark.parametrize('status,result,runs', [
    ('completed', '', [{'id': 1, 'status': 'completed'}]),
    ('completed', 'done', [{'id': 1, 'status': 'completed'}]),
    ('failed', '391', [{'id': 1, 'status': 'failed'}]),
    ('paused', '391', [{'id': 1, 'status': 'paused'}]),
    ('completed', '391', []),
    ('completed', '391', [{'id': 1, 'status': 'completed'}, {'id': 2, 'status': 'queued'}]),
])
def test_acceptance_never_turns_missing_wrong_or_ambiguous_evidence_into_pass(status, result, runs):
    assert not owner.successful_result({'task': {'status': status}, 'result': result, 'runs': runs})


def test_acceptance_positive_control_requires_correct_terminal_answer():
    assert owner.successful_result({'task': {'status': 'completed'}, 'result': '391',
                                    'runs': [{'id': 1, 'status': 'completed'}]})


def test_owner_required_report_is_nonzero_and_does_not_start_any_server(tmp_path, monkeypatch):
    def missing():
        raise owner.OwnerRequired('Install release wheel.')
    monkeypatch.setattr(owner, 'installed_identity', missing)
    def forbidden(*args, **kwargs):
        pytest.fail('Server must not start when installation evidence is missing')
    monkeypatch.setattr(owner, 'launch', forbidden)
    output = tmp_path / 'report.json'
    assert owner.main(['--data-dir', str(tmp_path), '--output', str(output)]) == 2
    result = json.loads(output.read_text())
    assert result['status'] == 'OWNER_REQUIRED'
    assert result['CORE_LIVE_API'] == 'NOT_RUN'
    assert result['fixture_adapter'] is False


def test_provider_error_details_never_leak_to_acceptance_report(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, 'installed_identity', lambda: {'source_sha': 'a' * 40})
    monkeypatch.setattr(owner, 'configured_model', lambda *args: {
        'agent_id': 1, 'model': {'name': 'model'},
        'provider': {'kind': 'openai_compat', 'base_url': 'https://user:secret@example.com/v1?token=secret'}})
    def failed(*args):
        raise RuntimeError('Authorization: Bearer raw-provider-secret')
    monkeypatch.setattr(owner, 'verify', failed)
    output = tmp_path / 'report.json'
    assert owner.main(['--data-dir', str(tmp_path), '--output', str(output)]) == 1
    text = output.read_text()
    assert 'secret' not in text and 'Authorization' not in text
    assert json.loads(text)['status'] == 'FAIL'
    assert json.loads(text)['provider_host'] == 'example.com'


@pytest.mark.parametrize('dirty', [True, None, 'false', 0, 1, 'missing'])
def test_installed_identity_refuses_unmeasured_or_dirty_source(tmp_path, monkeypatch, dirty):
    module = tmp_path / 'bcc' / 'owner_acceptance.py'
    module.parent.mkdir()
    module.write_text('')
    manifest = {'source_sha': 'a' * 40}
    if dirty != 'missing':
        manifest['source_dirty'] = dirty
    module.with_name('_build.json').write_text(json.dumps(manifest))
    class Distribution:
        version = '0.1'
        def read_text(self, name):
            return None
        def locate_file(self, name):
            return tmp_path / name
    monkeypatch.setattr(owner, '__file__', str(module))
    monkeypatch.setattr(owner.importlib.metadata, 'distribution', lambda name: Distribution())
    with pytest.raises(owner.OwnerRequired, match='clean source'):
        owner.installed_identity()


def test_clean_installed_identity_reports_exact_source(tmp_path, monkeypatch):
    module = tmp_path / 'bcc' / 'owner_acceptance.py'
    module.parent.mkdir()
    module.write_text('')
    module.with_name('_build.json').write_text(json.dumps({
        'source_sha': 'b' * 40, 'source_dirty': False}))
    class Distribution:
        version = '0.1'
        def read_text(self, name):
            return None
        def locate_file(self, name):
            return tmp_path / name
    monkeypatch.setattr(owner, '__file__', str(module))
    monkeypatch.setattr(owner.importlib.metadata, 'distribution', lambda name: Distribution())
    result = owner.installed_identity()
    assert result['source_sha'] == 'b' * 40
    assert result['source_dirty'] is False
    assert len(result['build_manifest_sha256']) == 64


# ------------------------------------------------ stop(): всё дерево, не один pid
# test_live_openrouter_dry_run на Windows падал при уборке temp: WinError 32 на
# bcc.db. Popen(sys.executable) в Windows venv запускает venvlauncher, а сервер —
# это ЕГО дочерний python.exe. terminate() убивал лаунчер, wait() возвращался, а
# настоящий сервер ещё держал bcc.db (его добивает job object лаунчера — позже,
# асинхронно). Модель здесь: «лаунчер», чей ребёнок держит bcc.db открытым.

_LAUNCHER = r'''
import subprocess, sys, time
from pathlib import Path
data = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, "-c",
    "import sys, time; f = open(sys.argv[1], 'ab'); open(sys.argv[2], 'w').write('ok'); time.sleep(120)",
    str(data / "bcc.db"), str(data / "child.ready")])
(data / "child.pid").write_text(str(child.pid))
child.wait()
'''


def test_stop_waits_for_the_whole_tree_that_holds_the_database(tmp_path):
    import subprocess
    import sys
    import time

    import psutil

    data = tmp_path / 'data'
    data.mkdir()
    process = subprocess.Popen([sys.executable, '-c', _LAUNCHER, str(data)])
    deadline = time.monotonic() + 30
    while not (data / 'child.ready').exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (data / 'child.ready').exists(), 'дочерний процесс не поднялся'
    child = psutil.Process(int((data / 'child.pid').read_text()))
    try:
        owner.stop(process)
        assert process.poll() is not None
        # Живой потомок с открытым bcc.db — ровно то, что на Windows даёт WinError 32.
        alive = child.is_running() and child.status() != psutil.STATUS_ZOMBIE
        assert not alive, f'потомок {child.pid} пережил stop() и держит bcc.db'
        (data / 'bcc.db').unlink()  # на Windows провалилось бы при живом держателе
    finally:
        with contextlib.suppress(psutil.Error):
            child.kill()


def test_stop_negative_control_leaves_unrelated_processes_alone(tmp_path):
    """Дерево снимается с pid сервера, а не «всё, что держит файлы рядом»."""
    import subprocess
    import sys

    import psutil

    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    server = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        owner.stop(server)
        assert server.poll() is not None
        assert psutil.Process(bystander.pid).is_running() and bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=10)


@pytest.mark.skipif(not os.path.isdir('/proc/self/fd'), reason='NOT RUN: счёт дескрипторов через /proc')
def test_configured_model_closes_the_owner_database_even_when_it_refuses(tmp_path):
    """Семейство утечек notifications/memory index: `with sqlite3.connect()` не
    закрывает соединение. Отказ OwnerRequired держит кадр в traceback, а кадр —
    открытый bcc.db владельца; на Windows это блокирует файл."""
    database = tmp_path / 'bcc.db'
    with contextlib.closing(sqlite3.connect(database)) as conn:
        conn.execute('CREATE TABLE agents (id INTEGER, enabled INTEGER, model_id INTEGER)')
        conn.execute('CREATE TABLE models (id INTEGER, provider_id INTEGER, health TEXT)')
        conn.execute('CREATE TABLE providers (id INTEGER, api_key_enc TEXT)')
        conn.commit()

    def holders() -> int:
        count = 0
        for fd in os.listdir('/proc/self/fd'):
            try:
                if os.readlink(f'/proc/self/fd/{fd}') == str(database.resolve()):
                    count += 1
            except OSError:
                pass
        return count

    with pytest.raises(owner.OwnerRequired) as kept:
        owner.configured_model(tmp_path, None)
    assert kept.value is not None
    assert holders() == 0, 'отказ держит bcc.db владельца открытым'
