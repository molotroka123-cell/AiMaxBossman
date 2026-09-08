"""Installed-only, genuine model-backed core acceptance in private isolated data.

    python -m bcc.owner_acceptance --data-dir <configured BCC data> --output report.json

Copies only one selected model/provider configuration into a new encrypted
vault, never owner tasks, schedules or the owner's vault master key. Owns both
server processes it starts. No fixture adapter, synthesized answer or fallback.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
import sqlalchemy as sa

from . import db as dbm, model_health
from .auth import TOKEN_FILE, _restrict_to_owner
from .secrets import KEY_ENV, KEY_FILE


class OwnerRequired(RuntimeError):
    pass


def installed_identity() -> dict:
    try:
        distribution = importlib.metadata.distribution('bossman-command-center')
    except importlib.metadata.PackageNotFoundError:
        raise OwnerRequired('Install the release wheel before running owner acceptance.') from None
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    if direct.get('dir_info', {}).get('editable'):
        raise OwnerRequired('Install the built wheel; editable/source installs are not accepted.')
    if Path(distribution.locate_file('bcc/owner_acceptance.py')).resolve() != Path(__file__).resolve():
        raise OwnerRequired('This module was loaded from a checkout instead of the installed artifact.')
    manifest = Path(__file__).with_name('_build.json')
    if not manifest.is_file():
        raise OwnerRequired('Installed artifact has no bcc/_build.json source attestation.')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    sha = data.get('source_sha', '')
    if not isinstance(sha, str) or re.fullmatch(r'[0-9a-f]{40}', sha) is None:
        raise OwnerRequired('Installed artifact must attest one full source SHA.')
    return {'source_sha': sha, 'version': distribution.version,
            'build_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}


def configured_model(data_dir: Path, agent_id: int | None) -> dict:
    """Read-only original SQLite. Returned secret stays in memory, never reports."""
    database = data_dir / 'bcc.db'
    if not database.is_file():
        raise OwnerRequired('Configure a real model and enabled agent in Bossman first.')
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        agents = [dict(row) for row in conn.execute('SELECT * FROM agents WHERE enabled = 1 ORDER BY id')]
        models = {row['id']: dict(row) for row in conn.execute('SELECT * FROM models')}
        providers = {row['id']: dict(row) for row in conn.execute('SELECT * FROM providers')}
    candidates = []
    for agent in agents:
        if agent_id is not None and agent['id'] != agent_id:
            continue
        model = models.get(agent.get('model_id'))
        provider = providers.get((model or {}).get('provider_id'))
        if model is None or provider is None:
            continue
        raw = json.loads(model.get('health') or '{}')
        health = model_health.HealthRecord.from_dict(raw)
        stamp = health.checked_at.timestamp() if health.checked_at else 0.0
        candidates.append((health.rank_key(), -stamp, agent['id'], agent, model, provider))
    if not candidates:
        raise OwnerRequired('No enabled agent with an existing model/provider; configure one and rerun.')
    _, _, _, agent, model, provider = min(candidates, key=lambda item: item[:3])
    encrypted = provider.get('api_key_enc')
    secret = None
    if encrypted:
        key = os.environ.get(KEY_ENV) or (
            (data_dir / KEY_FILE).read_text(encoding='utf-8') if (data_dir / KEY_FILE).is_file() else '')
        try:
            secret = Fernet(key.strip().encode()).decrypt(encrypted.encode()).decode()
        except (ValueError, InvalidToken, TypeError):
            raise OwnerRequired('The selected provider credential cannot be decrypted; repair the owner vault.') from None
    return {'agent_id': agent['id'], 'model': model, 'provider': provider, 'secret': secret}


def private_file(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
    _restrict_to_owner(path)


async def seed_private_registry(data_dir: Path, selected: dict) -> None:
    """New vault identity; no owner rows other than the chosen provider/model."""
    key = Fernet.generate_key()
    private_file(data_dir / KEY_FILE, key)
    encrypted = Fernet(key).encrypt(selected['secret'].encode()).decode() if selected['secret'] else None
    database = dbm.Database(f'sqlite+aiosqlite:///{data_dir / "bcc.db"}')
    try:
        await database.create_all()
        model = selected['model']
        provider = selected['provider']
        async with database.session() as session:
            await session.execute(sa.insert(dbm.providers).values(
                id=1, name='Owner acceptance provider', kind=provider['kind'],
                base_url=provider['base_url'], api_key_enc=encrypted))
            await session.execute(sa.insert(dbm.models).values(
                id=1, provider_id=1, name=model['name'], alias=model['alias'],
                kind=model.get('kind') or 'local', context_window=model.get('context_window') or 8192,
                caps=json.loads(model.get('caps') or '{}'),
                price_in=model.get('price_in'), price_out=model.get('price_out'),
                pricing_known=bool(model.get('pricing_known'))))
            await session.execute(sa.insert(dbm.agents).values(
                id=1, name='Owner acceptance', model_id=1, enabled=True, tools=[], permissions={},
                max_steps=1, max_tokens=256, fallback_model_id=None,
                system_prompt='Answer the arithmetic question exactly. Do not use tools.'))
            await session.commit()
    finally:
        await database.close()
    _restrict_to_owner(data_dir / 'bcc.db')


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def launch(data_dir: Path, port: int, log) -> subprocess.Popen:
    environment = os.environ.copy()
    for name in ('PYTHONPATH', 'PYTHONHOME', 'DATABASE_URL', KEY_ENV, 'BCC_UI_DIR', 'BCC_TOKEN_STDOUT'):
        environment.pop(name, None)
    environment['BCC_DATA_DIR'] = str(data_dir)
    return subprocess.Popen([sys.executable, '-I', '-m', 'bcc.app', '--host', '127.0.0.1', '--port', str(port)],
                            cwd=data_dir, env=environment, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=log)


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def connect(process, data_dir: Path, port: int) -> tuple[httpx.Client, dict]:
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False,
                          follow_redirects=False, timeout=10)
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Installed server exited before readiness.')
            try:
                response = client.get('/health/live')
                if response.status_code == 200 and response.json().get('alive') is True:
                    break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError('Installed server did not start within 60 seconds.')
        login = client.post('/api/login', json={'token': (data_dir / TOKEN_FILE).read_text().strip(),
                                              'label': 'owner-acceptance'})
        login.raise_for_status()
        client.headers['X-BCC-CSRF'] = login.json()['csrf']
        identity = client.get('/api/identity')
        identity.raise_for_status()
        return client, identity.json()
    except BaseException:
        client.close()
        raise


def successful_result(data: dict) -> bool:
    runs = data.get('runs') or []
    return (data.get('task', {}).get('status') == 'completed' and
            str(data.get('result') or '').strip() == '391' and len(runs) == 1 and
            runs[0].get('status') == 'completed')


def verify(selected: dict, report: dict, timeout: float) -> None:
    with tempfile.TemporaryDirectory(prefix='bossman-owner-acceptance-') as temporary:
        data_dir = Path(temporary)
        os.chmod(data_dir, 0o700)
        _restrict_to_owner(data_dir)
        asyncio.run(seed_private_registry(data_dir, selected))
        selected['secret'] = None
        port = free_port()
        with (data_dir / 'server.log').open('wb') as log:
            process = launch(data_dir, port, log)
            client = None
            try:
                client, initial_identity = connect(process, data_dir, port)
                ui = client.get('/')
                ui.raise_for_status()
                if '<html' not in ui.text.lower():
                    raise RuntimeError('Installed UI did not return HTML.')
                report['BOOT'] = 'PASS'
                response = client.post('/api/tasks', json={
                    'title': 'Owner live acceptance 17*23', 'agent_id': 1, 'run_now': True, 'max_retries': 0,
                    'prompt': 'Посчитай 17*23. Ответь только числом. Не используй инструменты и не пиши файлы.'})
                response.raise_for_status()
                task_id = response.json()['task']['id']
                deadline = time.monotonic() + timeout
                result = {}
                while time.monotonic() < deadline:
                    response = client.get(f'/api/tasks/{task_id}')
                    response.raise_for_status()
                    result = response.json()
                    if result['task']['status'] in {'completed', 'failed', 'stopped', 'blocked', 'paused', 'waiting_approval'}:
                        break
                    time.sleep(0.5)
                report['task_id'] = task_id
                report['terminal_status'] = result.get('task', {}).get('status')
                if not successful_result(result):
                    raise RuntimeError('Real model task did not complete with exactly 391 in one run.')
                report['CORE_LIVE_API'] = 'PASS'
                report['answer'] = '391'
                report['run_id'] = result['runs'][0]['id']
                report['usage'] = {name: result['runs'][0].get(name)
                                   for name in ('tokens_in', 'tokens_out', 'cost_usd', 'model_alias')}
                client.close()
                client = None
                stop(process)
                process = launch(data_dir, port, log)
                client, restarted_identity = connect(process, data_dir, port)
                after = client.get(f'/api/tasks/{task_id}')
                after.raise_for_status()
                after_data = after.json()
                if (not successful_result(after_data) or after_data['runs'][0]['id'] != report['run_id']
                        or restarted_identity.get('started_at') == initial_identity.get('started_at')):
                    raise RuntimeError('Restart did not preserve the completed task and original run.')
                report['RESTART_PERSISTENCE'] = 'PASS'
            finally:
                if client is not None:
                    client.close()
                stop(process)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True, help='Existing configured BCC data; read only')
    parser.add_argument('--agent-id', type=int)
    parser.add_argument('--output', type=Path, default=Path('bossman-owner-task-acceptance.json'))
    parser.add_argument('--timeout', type=float, default=180, help='Maximum model task seconds (1..600)')
    args = parser.parse_args(argv)
    report = {'status': 'FAIL', 'BOOT': 'NOT_RUN', 'CORE_LIVE_API': 'NOT_RUN',
              'RESTART_PERSISTENCE': 'NOT_RUN', 'CORE_UI': 'OWNER_REQUIRED',
              'scope': 'installed_core_model_and_restart',
              'timestamp': dbm.utcnow().isoformat() + 'Z', 'fixture_adapter': False}
    exit_code = 1
    try:
        if not 1 <= args.timeout <= 600:
            raise ValueError('Timeout must be between 1 and 600 seconds.')
        report.update(installed_identity())
        selected = configured_model(args.data_dir.expanduser().resolve(), args.agent_id)
        report.update(model=selected['model']['name'], provider=selected['provider']['kind'],
                      provider_host=urlsplit(selected['provider']['base_url']).hostname,
                      original_agent_id=selected['agent_id'])
        verify(selected, report, args.timeout)
        report['status'], exit_code = 'PASS', 0
    except OwnerRequired as exc:
        report.update(status='OWNER_REQUIRED', reason=str(exc))
        exit_code = 2
    except (Exception, KeyboardInterrupt) as exc:
        # Provider response bodies may contain secrets; never copy exception
        # messages or server logs into a shareable acceptance report.
        report.update(status='FAIL', error_type=type(exc).__name__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"{report['status']}: {args.output.resolve()}")
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
