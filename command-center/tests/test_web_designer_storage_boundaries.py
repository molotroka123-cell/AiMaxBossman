"""Real filesystem and two-process negative controls for Web Designer storage."""
from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
import threading

import pytest

from bcc.features import web_designer as wd


def _link(source: Path, target: Path, *, directory=False):
    if os.name == 'nt' and directory:
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(source), str(target)],
                       check=True, capture_output=True, text=True)
    else:
        source.symlink_to(target, target_is_directory=directory)


@pytest.mark.parametrize('member', ['project', 'current.html', 'project.json', 'history'])
async def test_linked_project_content_is_refused_without_read_or_write_outside(env, tmp_path, member):
    created = (await env.client.post('/api/web-designer/projects',
               json={'name': 'Safe project', 'template': 'blank'})).json()
    pid = created['meta']['id']
    pdir = wd._pdir(env.svc, pid)
    outside = tmp_path / 'outside'
    outside.mkdir()
    secret = '<html><body>OUTSIDE MUST STAY PRIVATE AND UNCHANGED</body></html>'
    (outside / 'current.html').write_text(secret)
    (outside / 'project.json').write_text(json.dumps({**created['meta'], 'name': 'PRIVATE EXTERNAL'}))
    (outside / 'v1.html').write_text(secret)
    if member == 'project':
        saved = pdir.with_name(pdir.name + '.original')
        pdir.rename(saved)
        _link(pdir, outside, directory=True)
    else:
        target = pdir / member
        if target.is_dir():
            target.rename(target.with_name(member + '.original'))
        else:
            target.unlink()
        _link(target, outside if member == 'history' else outside / member,
              directory=member == 'history')
    before = {path.name: path.read_bytes() for path in outside.iterdir()}
    read = await env.client.get(f'/api/web-designer/projects/{pid}')
    assert read.status_code == 403
    assert 'OUTSIDE MUST' not in read.text and 'PRIVATE EXTERNAL' not in read.text
    write = await env.client.put(f'/api/web-designer/projects/{pid}/code',
                                json={'html': '<html><body>ATTACK</body></html>'})
    assert write.status_code == 403
    restore = await env.client.post(f'/api/web-designer/projects/{pid}/versions/1/restore')
    assert restore.status_code == 403
    assert {path.name: path.read_bytes() for path in outside.iterdir()} == before
    # A planted unsafe project must not stop safe projects appearing in the list.
    listed = await env.client.get('/api/web-designer/projects')
    assert listed.status_code == 200
    assert 'PRIVATE EXTERNAL' not in listed.text


PROCESS = r'''
import asyncio, json, sys, time
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException
from bcc.features import web_designer as wd
data, role = Path(sys.argv[1]), sys.argv[2]
svc = SimpleNamespace(settings=SimpleNamespace(data_dir=data))
pdir = wd._pdir(svc, 1)
original = wd._load_meta
def read_and_hold(path):
    value = original(path)
    if role == 'a':
        (data / 'a-read').write_text('ready')
        deadline = time.monotonic() + 10
        while not (data / 'release-a').exists():
            if time.monotonic() > deadline: raise RuntimeError('parent did not release writer')
            time.sleep(.01)
    return value
wd._load_meta = read_and_hold
async def run():
    (data / (role + '-started')).write_text('started')
    try:
        async with wd._project_lock(pdir):
            result = wd._save_code(svc, pdir, '<html><body>' + role + '</body></html>', role,
                                   expect_version=1)
        print(json.dumps({'status': 200, 'version': result['version']}))
    except HTTPException as e:
        print(json.dumps({'status': e.status_code}))
asyncio.run(run())
'''


def _wait_for(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f'child did not reach {path.name}'
        time.sleep(.02)


def test_two_processes_cannot_commit_the_same_base_version(tmp_path):
    from types import SimpleNamespace

    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    pdir = wd._pdir(svc, 1)
    wd._save_code(svc, pdir, '<html><body>base</body></html>', 'seed')
    children = []
    try:
        a = subprocess.Popen([sys.executable, '-c', PROCESS, str(tmp_path), 'a'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(a)
        _wait_for(tmp_path / 'a-read')
        b = subprocess.Popen([sys.executable, '-c', PROCESS, str(tmp_path), 'b'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(b)
        _wait_for(tmp_path / 'b-started')
        # Unfixed B can commit while A holds an already-read version. Fixed B
        # waits for the OS lock; the bound prevents a deadlocking test.
        try:
            b.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        (tmp_path / 'release-a').write_text('release')
        results = []
        for process in children:
            output, error = process.communicate(timeout=12)
            assert process.returncode == 0, error
            results.append(json.loads(output))
        assert sorted(result['status'] for result in results) == [200, 409], results
        assert wd._load_meta(pdir)['version'] == 2
        assert wd._read_code(pdir) == '<html><body>a</body></html>'
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


async def test_metadata_failure_never_exposes_uncommitted_code(env, monkeypatch):
    created = (await env.client.post('/api/web-designer/projects',
               json={'name': 'Crash-safe project', 'template': 'blank'})).json()
    pid = created['meta']['id']
    pdir = wd._pdir(env.svc, pid)
    before = wd._read_code(pdir)
    version = created['meta']['version']
    real_save = wd._save_meta

    def crash_before_commit(path, meta):
        raise OSError('crash after current.html installation, before metadata commit')

    monkeypatch.setattr(wd, '_save_meta', crash_before_commit)
    with pytest.raises(OSError, match='before metadata commit'):
        wd._save_code(env.svc, pdir, '<html><body>UNCOMMITTED</body></html>', 'crash',
                      expect_version=version)
    assert wd._read_code(pdir) == before
    actual = (await env.client.get(f'/api/web-designer/projects/{pid}')).json()
    assert actual['meta']['version'] == version
    assert actual['code'] == before
    monkeypatch.setattr(wd, '_save_meta', real_save)
    code = '<html><body>COMMITTED AFTER RECOVERY</body></html>'
    saved = await env.client.put(f'/api/web-designer/projects/{pid}/code',
                                json={'html': code, 'base_version': version})
    assert saved.status_code == 200
    assert saved.json()['meta']['version'] == version + 1
    assert wd._read_code(pdir) == code


async def test_cancelled_waiter_does_not_leave_a_late_acquired_lock(tmp_path, monkeypatch):
    from bossman_shared.fable_budget import _CrossProcessFileLock

    pdir = tmp_path / 'web_designer' / '1'
    holder = _CrossProcessFileLock(pdir.parent / '.locks' / pdir.name)
    holder.__enter__()
    entered = threading.Event()
    original = wd._ProjectLock._acquire

    def observed_acquire(self):
        entered.set()
        original(self)

    monkeypatch.setattr(wd._ProjectLock, '_acquire', observed_acquire)
    lock = wd._project_lock(pdir)

    async def waiting_write():
        async with lock:
            raise AssertionError('cancelled waiter must not perform a write')

    task = asyncio.create_task(waiting_write())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.05)
    finally:
        holder.__exit__(None, None, None)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert not lock.locked()
    async with asyncio.timeout(5):
        async with wd._project_lock(pdir):
            pass


CREATE_PROCESS = r'''
import asyncio, json, sys, time
from pathlib import Path
from types import SimpleNamespace
from bcc.features import web_designer as wd
data, role = Path(sys.argv[1]), sys.argv[2]
svc = SimpleNamespace(settings=SimpleNamespace(data_dir=data))
request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=svc)))
original = wd._next_id
def allocate_and_hold(service):
    value = original(service)
    if role == 'a':
        (data / 'a-read').write_text('ready')
        deadline = time.monotonic() + 10
        while not (data / 'release-a').exists():
            if time.monotonic() > deadline: raise RuntimeError('parent did not release creator')
            time.sleep(.01)
    return value
wd._next_id = allocate_and_hold
async def run():
    (data / (role + '-started')).write_text('started')
    result = await wd.create_project(wd.ProjectIn(name=role, template='blank'), request)
    print(json.dumps({'id': result['meta']['id']}))
asyncio.run(run())
'''


def test_two_processes_allocate_distinct_projects(tmp_path):
    children = []
    try:
        a = subprocess.Popen([sys.executable, '-c', CREATE_PROCESS, str(tmp_path), 'a'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(a)
        _wait_for(tmp_path / 'a-read')
        b = subprocess.Popen([sys.executable, '-c', CREATE_PROCESS, str(tmp_path), 'b'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(b)
        _wait_for(tmp_path / 'b-started')
        try:
            b.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        (tmp_path / 'release-a').write_text('release')
        identifiers = []
        for process in children:
            output, error = process.communicate(timeout=12)
            assert process.returncode == 0, error
            identifiers.append(json.loads(output)['id'])
        assert sorted(identifiers) == [1, 2]
        assert wd._load_meta(tmp_path / 'web_designer' / '1')['name'] == 'a'
        assert wd._load_meta(tmp_path / 'web_designer' / '2')['name'] == 'b'
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


@pytest.mark.parametrize('operation', ['save', 'restore'])
async def test_delete_winning_the_lock_cannot_be_undone_by_a_stale_request(env, monkeypatch, operation):
    created = (await env.client.post('/api/web-designer/projects',
               json={'name': 'Must stay deleted', 'template': 'blank'})).json()
    pid = created['meta']['id']
    pdir = wd._pdir(env.svc, pid)
    original = wd._project_lock
    validated = asyncio.Event()
    proceed = asyncio.Event()
    first = True

    class DelayedLock:
        def __init__(self):
            self.lock = original(pdir)

        async def __aenter__(self):
            validated.set()
            await proceed.wait()
            return await self.lock.__aenter__()

        async def __aexit__(self, *args):
            return await self.lock.__aexit__(*args)

    def delay_first(path):
        nonlocal first
        if path == pdir and first:
            first = False
            return DelayedLock()
        return original(path)

    monkeypatch.setattr(wd, '_project_lock', delay_first)
    call = (env.client.put(f'/api/web-designer/projects/{pid}/code',
                          json={'html': '<html><body>STALE SAVE</body></html>'})
            if operation == 'save' else
            env.client.post(f'/api/web-designer/projects/{pid}/versions/1/restore'))
    pending = asyncio.create_task(call)
    try:
        await asyncio.wait_for(validated.wait(), timeout=5)
        deleted = await env.client.delete(f'/api/web-designer/projects/{pid}')
        assert deleted.status_code == 200 and not pdir.exists()
        proceed.set()
        stale = await asyncio.wait_for(pending, timeout=5)
        assert stale.status_code == 404
        assert not pdir.exists(), 'stale request resurrected an already deleted project'
        assert (await env.client.get(f'/api/web-designer/projects/{pid}')).status_code == 404
    finally:
        proceed.set()
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
