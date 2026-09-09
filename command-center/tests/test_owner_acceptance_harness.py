"""Harness safety/integrity; these tests never substitute for a live model run."""
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
