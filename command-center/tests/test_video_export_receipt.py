"""Real-media export proof survives gates/restart, but not tamper or cross-run replay."""
import asyncio
import copy
from pathlib import Path
import shutil
from types import SimpleNamespace
import pytest
import sqlalchemy as sa
from bcc.db import tasks as tasks_t, task_runs as runs_t
from bcc.video_studio import render
from bcc.video_studio.media import binary, process
from bcc.video_studio.service import VideoService, jobs
from bcc.video_studio.export_receipt import RECEIPT_KEY
from .test_video_studio_integration import BASE, op, execute_task

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="real FFmpeg binaries required")


async def render_once(env, tmp_path, monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(total=16*1024**3, available=8*1024**3))
    source = tmp_path / 'input.mp4'
    await process([binary('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
        'color=blue:size=64x64:rate=25:duration=0.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)])
    chat = (await env.client.post(BASE+'/chat', json={'text':'Склей видео', 'operation_id':op()})).json()
    pid = chat['project_id']
    upload = await env.client.post(BASE+'/media', params={'project_id':pid, 'filename':'input.mp4',
        'expected_revision':0, 'operation_id':op()}, content=source.read_bytes())
    assert upload.status_code == 200, upload.text
    await env.client.post(BASE+f"/chat/{chat['task_id']}/run")
    assert (await execute_task(env, chat['task_id']))['task']['status'] == 'completed'
    project = await env.svc.video_studio.store.get(pid)
    job = await env.svc.video_studio.export({'project_id':pid, 'expected_revision':project['revision'],
        'operation_id':op(), 'options':{'width':64, 'height':64}})
    done = await execute_task(env, job['task_id'])
    assert done['task']['status'] == 'completed', done
    async with env.svc.db.session() as s:
        row = dict((await s.execute(sa.select(jobs).where(jobs.c.id == job['job_id']))).mappings().one())
        task = dict((await s.execute(sa.select(tasks_t).where(tasks_t.c.id == job['task_id']))).mappings().one())
        run_id = (await s.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == task['id']))).scalar_one()
    return row, task, run_id


@needs_ffmpeg
async def test_full_decode_only_once_and_never_in_completion_hook(env, tmp_path, monkeypatch):
    original, calls = render.verify_output, []
    async def measured(*args, **kwargs):
        calls.append(args[0])
        return await original(*args, **kwargs)
    monkeypatch.setattr(render, 'verify_output', measured)
    row, task, run_id = await render_once(env, tmp_path, monkeypatch)
    assert len(calls) == 1  # Previously the critical hook decoded the entire export again.
    async def forbidden(*args, **kwargs):
        raise AssertionError('completion gate must not decode or hash the full output')
    monkeypatch.setattr(render, 'verify_output', forbidden)
    from bcc.video_studio import export_receipt
    if not export_receipt.REQUIRES_CONTENT_RECHECK:
        monkeypatch.setattr(export_receipt, 'digest_file', forbidden)
    verdict = await env.svc.video_studio.render_gate(task, run_id, 'model said done')
    assert verdict['verdict'] == 'PASS'
    current = await env.svc.video_studio.job(row['id'])
    assert current['output_url'] and RECEIPT_KEY not in current


@needs_ffmpeg
async def test_durable_receipt_survives_service_restart_not_wrong_run(env, tmp_path, monkeypatch):
    row, task, run_id = await render_once(env, tmp_path, monkeypatch)
    restarted = VideoService(env.svc)
    assert (await restarted.render_gate(task, run_id, ''))['verdict'] == 'PASS'
    assert (await restarted.render_gate(task, run_id+1, ''))['verdict'] == 'FAIL'
    assert (await restarted.render_gate({**task, 'id':task['id']+1}, run_id, ''))['verdict'] == 'FAIL'


@needs_ffmpeg
@pytest.mark.parametrize('change', ['unsigned', 'verification', 'snapshot', 'options', 'path', 'same-size-bytes'])
async def test_receipt_is_not_transferable_or_a_mutable_trust_cache(env, tmp_path, monkeypatch, change):
    import os
    row, task, run_id = await render_once(env, tmp_path, monkeypatch)
    result = copy.deepcopy(row['result'])
    values = {'result':result}
    if change == 'unsigned':
        result.pop(RECEIPT_KEY)
    elif change == 'verification':
        result['verification']['width'] += 2
    elif change == 'snapshot':
        values['snapshot'] = {**row['snapshot'], 'revision':row['snapshot']['revision']+1}
    elif change == 'options':
        values['options'] = {**row['options'], 'width':128}
    elif change == 'path':
        other = Path(result['path']).with_name('substitute.mp4')
        other.write_bytes(Path(result['path']).read_bytes())
        result['path'] = str(other)
    else:
        path = Path(result['path']); stat = path.stat()
        data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    async with env.svc.db.session() as s:
        await s.execute(sa.update(jobs).where(jobs.c.id == row['id']).values(**values)); await s.commit()
    assert (await env.svc.video_studio.render_gate(task, run_id, 'done'))['verdict'] == 'FAIL'


async def test_timeout_retains_safe_actionable_diagnostics(env, monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(total=16*1024**3, available=8*1024**3))
    async def fail(*args, **kwargs):
        raise TimeoutError('private path and token must never appear in API diagnostics')
    monkeypatch.setattr(render, 'render_project', fail)
    made = await env.svc.video_studio.store.create('error-project', 'QA', 'create-error-project')
    job = await env.svc.video_studio.export({'project_id':made['project']['id'], 'expected_revision':0,
        'operation_id':op(), 'options':{}})
    assert (await execute_task(env, job['task_id']))['task']['status'] == 'failed'
    current = await env.svc.video_studio.job(job['job_id'])
    assert current['error_detail']['code'] == 'TIMEOUT'
    assert 'private path' not in str(current)
    assert current['output_url'] is None


@needs_ffmpeg
async def test_restored_metadata_cannot_bypass_windows_integrity_recheck(env,tmp_path,monkeypatch):
    from bcc.video_studio import export_receipt
    row,task,run_id=await render_once(env,tmp_path,monkeypatch)
    result=row["result"]
    original=result[RECEIPT_KEY]["file_identity"]
    path=Path(result["path"]);data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
    monkeypatch.setattr(export_receipt,"REQUIRES_CONTENT_RECHECK",True)
    monkeypatch.setattr(export_receipt,"file_identity",lambda _:original)
    # Signed metadata alone still matches; the portable content check must reject.
    export_receipt.validate(env.svc.video_studio.root,row,task["id"],run_id,result)
    assert (await env.svc.video_studio.render_gate(task,run_id,"done"))["verdict"]=="FAIL"
