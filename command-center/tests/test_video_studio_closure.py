"""Regression evidence for the audited export, compute and duration failures."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
import sqlalchemy as sa
from bcc.db import tasks as tasks_t
from bcc.video_studio.service import jobs
from bcc.video_studio.artifacts import matches, seal
from bcc.video_studio.media import digest_file, verified_digest, MediaLibrary
from bcc.video_studio.model import clip_duration, TICKS
from bcc.video_studio.render import clip_duration as render_duration
from .test_video_studio_render import fixture_media, document


def test_hash_cache_detects_same_size_rewrite_with_restored_timestamp(tmp_path, monkeypatch):
    from bcc.video_studio import media
    path=tmp_path/"artifact.bin"
    path.write_bytes(b"original")
    before=path.stat()
    calls=[]
    original=media.digest_file
    def measured(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(media,"digest_file",measured)
    digest=verified_digest(path)
    receipt=seal(path,digest)
    assert verified_digest(path)==digest
    assert len(calls)==2  # cache miss and independent receipt seal
    path.write_bytes(b"tampered")
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert verified_digest(path)!=digest
    assert not matches(path,receipt,digest)


@pytest.mark.parametrize("clip",[{"adjustment":True},{"title":{"text":"TEST"}},
    {"freeze":True,"freeze_duration":750000},
    {"source_in":0,"source_out":1,"speed_ramp":[
        {"source_in":0,"source_out":1,"speed":{"num":3,"den":1}},
        {"source_in":1,"source_out":3,"speed":{"num":3,"den":1}}]}])
def test_duration_contract_shared_by_model_and_renderer(clip):
    assert render_duration(clip)==clip_duration(clip)/TICKS


async def test_preparation_burst_deduplicates_real_ffmpeg(tmp_path,monkeypatch):
    from bcc.video_studio import media as module
    item=await fixture_media(tmp_path)
    library=MediaLibrary(tmp_path)
    calls=[]
    original=module.process
    async def counted(argv,**kwargs):
        calls.append(argv)
        return await original(argv,**kwargs)
    monkeypatch.setattr(module,"process",counted)
    results=await asyncio.gather(*(library.prepare(item) for _ in range(12)))
    assert all(value==results[0] for value in results)
    assert len(calls)==3  # thumbnail, proxy, waveform once each
    assert all((tmp_path/path).stat().st_size>0 for path in results[0].values())


async def test_render_receipt_gate_is_bounded_and_retry_recovers_bytes(env,monkeypatch):
    from bcc.video_studio import render
    video=env.svc.video_studio
    media=await fixture_media(video.root)
    project=document(media)
    # Seed only the host-owned durable job; real renderer/decoder/hashes run below.
    async with env.svc.db.session() as session:
        task_id=(await session.execute(sa.insert(tasks_t).values(title="Receipt TEST",prompt="TEST",kind="video_render",status="running"))).inserted_primary_key[0]
        await session.execute(sa.insert(jobs).values(id="receipt-test",operation_id="receipt-test",digest="a"*64,
            project_id=project["id"],task_id=task_id,snapshot=project,options={"_container":"mp4"}))
        await session.commit()
    task={"id":task_id,"kind":"video_render","meta":{"video_job_id":"receipt-test"}}
    async def fence(run_id): pass
    engine=SimpleNamespace(assert_fence=fence)
    await video.render_executor(task,{"id":123},engine)
    async with env.svc.db.session() as session:
        first=(await session.execute(sa.select(jobs.c.result).where(jobs.c.id=="receipt-test"))).scalar_one()
    original_bytes=Path(first["path"]).read_bytes()
    async def forbidden(*args,**kwargs):
        raise AssertionError("critical hook must not decode or rerender")
    original_verify=render.verify_output
    monkeypatch.setattr(render,"verify_output",forbidden)
    assert (await asyncio.wait_for(video.render_gate(task,123,"verified"),1))["verdict"]=="PASS"
    assert (await video.render_gate(task,124,"stale"))["verdict"]=="FAIL"
    monkeypatch.setattr(render,"verify_output",original_verify)
    monkeypatch.setattr(render,"render_project",forbidden)
    await video.render_executor(task,{"id":124},engine)
    assert (await video.render_gate(task,124,"recovered"))["verdict"]=="PASS"
    assert Path(first["path"]).read_bytes()==original_bytes
    Path(first["path"]).write_bytes(b"corrupt")
    assert (await video.render_gate(task,124,"changed"))["verdict"]=="FAIL"


def test_media_failures_are_structured_and_secret_safe():
    from bcc.video_studio.errors import failure_details, MediaFailure
    details=failure_details(ValueError("verification failed secret-token=abc C:/private/owner"))
    assert details["category"]=="VERIFY_FAILED"
    assert "abc" not in str(details) and "private" not in str(details)
    assert details["technical_context_id"] and details["retryable"]
    assert failure_details(MediaFailure("FFMPEG_MISSING"))["owner_action_required"]


async def test_thumbnail_get_burst_never_runs_preparation(env,monkeypatch):
    video=env.svc.video_studio
    created=await video.store.create("thumb-test","TEST","thumb-create")
    media=await fixture_media(video.root)
    await video.command({"project_id":"thumb-test","expected_revision":created["revision"],
        "operation_id":"thumb-media","command":{"type":"media.import","media":media}},trusted_media=True)
    async def forbidden(*args,**kwargs):
        raise AssertionError("GET must never launch a derivative job")
    monkeypatch.setattr(video.media,"prepare",forbidden)
    url=f"/api/video-studio/media/{media['id']}/thumbnail?project_id=thumb-test"
    responses=await asyncio.gather(*(env.client.get(url) for _ in range(12)))
    assert all(response.status_code==409 for response in responses)


async def test_missing_encoder_job_exposes_durable_safe_failure(env,monkeypatch):
    from bcc.video_studio.errors import MediaFailure
    video=env.svc.video_studio
    await video.store.create("failure-test","TEST","failure-create")
    job=await video.export({"project_id":"failure-test","expected_revision":0,
        "operation_id":"failure-export","options":{}})
    async with env.svc.db.session() as session:
        task=dict((await session.execute(sa.select(tasks_t).where(tasks_t.c.id==job["task_id"]))).mappings().one())
    async def fence(run_id): pass
    async def missing(*args,**kwargs):raise MediaFailure("FFMPEG_MISSING","initialization")
    # Rebind the registered wrapper to the injected real host-service dependency.
    monkeypatch.setattr(video,"render_executor",missing)
    from bcc.features.video_studio import setup
    # setup constructs the service, so preserve the already configured instance.
    import bcc.features.video_studio as feature
    monkeypatch.setattr(feature,"VideoService",lambda svc:video)
    await setup(env.svc)
    with pytest.raises(MediaFailure):
        await env.svc.engine.executors["video_render"](task,{"id":1},SimpleNamespace(assert_fence=fence))
    observed=await video.job(job["job_id"])
    assert observed["failure"]["category"]=="FFMPEG_MISSING"
    assert observed["failure"]["stage"]=="initialization"
    assert observed["failure"]["owner_action_required"] is True
    assert observed["output_url"] is None and "Install FFmpeg" in observed["error"]


async def test_export_revision_conflict_remains_structured(env):
    await env.svc.video_studio.store.create("conflict-test","TEST","conflict-create")
    response=await env.client.post("/api/video-studio/exports",json={"project_id":"conflict-test",
        "expected_revision":1,"operation_id":"conflict-export","options":{}})
    assert response.status_code==409
    assert response.json()["error"]["code"]=="revision_conflict"
