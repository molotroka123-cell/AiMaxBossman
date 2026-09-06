"""Real authenticated integration, shared commands, queue, media and negative paths."""
import asyncio
import json
import shutil
import subprocess
import uuid
import httpx
import pytest
import sqlalchemy as sa
from bcc.db import tasks as tasks_t, task_runs as runs_t
from bcc.tools import REGISTRY, ToolContext, allowed_tools_for, decide_effect
from bcc.video_studio.service import editing_intent

BASE="/api/video-studio"
def op(): return uuid.uuid4().hex

@pytest.mark.parametrize("text,expected",[("Склей эти два видео",True),("Сделай Reels из этого ролика",True),
    ("Добавь русские субтитры",True),("Убери паузы и шум из видео",True),
    ("Сделай рекламу Fresh Vibes на 30 секунд",True),("Открой этот проект и замени музыку",True),
    ("Как монтировать видео?",False),("Explain how to edit video",False),("Расскажи про Reels",False)])
def test_intents(text,expected): assert editing_intent(text) is expected

async def create(env):
    r=await env.client.post(BASE+"/projects",json={"name":"QA","operation_id":op()})
    assert r.status_code==200,r.text
    return r.json()["project"]

async def test_auth_and_chat_idempotency(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),base_url="http://test") as client:
        assert (await client.get(BASE+"/projects")).status_code==401
        assert (await client.post(BASE+"/chat",json={"text":"Склей видео","operation_id":op()})).status_code==401
    payload={"text":"Склей эти два видео","operation_id":op()}
    first=await env.client.post(BASE+"/chat",json=payload)
    assert first.status_code==200,first.text
    again=await env.client.post(BASE+"/chat",json=payload)
    assert again.json()==first.json()
    bad=await env.client.post(BASE+"/chat",json={**payload,"text":"Сделай Reels"})
    assert bad.status_code==409
    theory=await env.client.post(BASE+"/chat",json={"text":"Как монтировать видео?","operation_id":op()})
    assert theory.json()=={"handled":False}
    pid=first.json()["project_id"]
    assert (await env.client.get(BASE+"/projects/"+pid)).status_code==200

async def test_command_revision_dryrun_replay_and_media_forgery(env):
    project=await create(env)
    body={"project_id":project["id"],"expected_revision":0,"operation_id":op(),
          "command":{"type":"project.rename","name":"After"}}
    dry=await env.client.post(BASE+"/commands",json={**body,"dry_run":True})
    assert dry.status_code==200,dry.text
    assert (await env.client.get(BASE+"/projects/"+project["id"])).json()["revision"]==0
    actual=await env.client.post(BASE+"/commands",json=body)
    assert actual.status_code==200,actual.text
    replay=await env.client.post(BASE+"/commands",json=body)
    assert replay.json()==actual.json()
    stale=await env.client.post(BASE+"/commands",json={**body,"operation_id":op()})
    assert stale.status_code==409,stale.text
    forged=await env.client.post(BASE+"/commands",json={**body,"expected_revision":1,"operation_id":op(),
          "command":{"type":"timeline.apply","operations":[{"type":"media.import","media":{"relative_path":"../../private"}}]}})
    assert forged.status_code==403

async def test_tools_preserve_authority_and_shared_store(env):
    project=await create(env)
    task={"id":999,"meta":{"video_project_id":project["id"]}}
    agent={"tools":[],"permissions":{}}
    spec=REGISTRY.get("video.timeline.apply")
    assert allowed_tools_for(task,agent)==[]
    effect,_=decide_effect(spec,{},agent)
    assert effect=="ask"
    ctx=ToolContext(svc=env.svc,task=task,run_id=0,agent=agent)
    value=await spec.handler({"project_id":project["id"],"expected_revision":0,"operation_id":op(),
        "command":{"type":"project.rename","name":"Agent edit"}},ctx)
    assert not value.error
    current=(await env.client.get(BASE+"/projects/"+project["id"])).json()
    assert current["name"]=="Agent edit" and current["revision"]==1
    with pytest.raises(PermissionError):
        await REGISTRY.get("video.project.inspect").handler({"project_id":"other"},ctx)

async def execute_task(env,task_id):
    async with env.svc.db.session() as s:
        run=(await s.execute(sa.select(runs_t).where(runs_t.c.task_id==task_id).order_by(runs_t.c.id.desc()))).mappings().first()
    assert run
    await env.svc.engine.execute(run["id"])
    return (await env.client.get(f"/api/tasks/{task_id}")).json()

@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="local FFmpeg unavailable")
async def test_actual_chat_upload_edit_preview_export(env,tmp_path,monkeypatch):
    # Deterministic admission telemetry; FFmpeg, files, DB and gates remain real.
    # A separate test exercises measured low/unknown memory fail-closed behavior.
    from types import SimpleNamespace
    import psutil
    import os
    if not os.environ.get("VIDEO_TEST_REAL_MEMORY"):
        monkeypatch.setattr(psutil,"virtual_memory",lambda:SimpleNamespace(total=16*1024**3,available=8*1024**3))
    fixture=tmp_path/"input.mp4"
    subprocess.run([shutil.which("ffmpeg"),"-v","error","-f","lavfi","-i","color=c=blue:s=160x90:r=25:d=0.4",
                    "-c:v","libx264","-pix_fmt","yuv420p","-y",str(fixture)],check=True,timeout=30)
    chat=(await env.client.post(BASE+"/chat",json={"text":"Склей эти два видео","operation_id":op()})).json()
    pid=chat["project_id"]
    upload=await env.client.post(BASE+"/media",params={"project_id":pid,"filename":"input.mp4",
        "expected_revision":0,"operation_id":op()},content=fixture.read_bytes())
    assert upload.status_code==200,upload.text
    await env.client.post(BASE+f"/chat/{chat['task_id']}/run")
    task=await execute_task(env,chat["task_id"])
    assert task["task"]["status"]=="completed",task
    project=(await env.client.get(BASE+"/projects/"+pid)).json()
    assert len(project["sequences"][0]["tracks"][0]["clips"])==1
    for preview in (True,False):
        request={"project_id":pid,"expected_revision":project["revision"],"operation_id":op(),"preview":preview,
                 "options":{"width":160,"height":90}}
        response=await env.client.post(BASE+"/exports",json=request)
        assert response.status_code==200,response.text
        job=response.json()
        assert job["status"]=="queued"
        done=await execute_task(env,job["task_id"])
        assert done["task"]["status"]=="completed",done
        status=(await env.client.get(BASE+"/exports/"+job["job_id"])).json()
        assert status["verification"]["passed"] and status["output_url"]
        download=await env.client.get(status["output_url"])
        assert download.status_code==200 and len(download.content)>100
        replay=(await env.client.post(BASE+"/exports",json=request)).json()
        assert replay["job_id"]==job["job_id"]
        from bcc.video_studio.service import jobs
        async with env.svc.db.session() as session:
            result=(await session.execute(sa.select(jobs.c.result).where(jobs.c.id==job["job_id"]))).scalar_one()
        from pathlib import Path
        with Path(result["path"]).open("ab") as output:
            output.write(b"tampered after verification")
        refused=await env.client.get(status["output_url"])
        assert refused.status_code==409,refused.text


    analysis=await env.client.post(BASE+"/analysis",json={"project_id":pid,"media_id":upload.json()["media"]["id"],
        "expected_revision":project["revision"],"operation_id":op(),"action":"analyse"})
    assert analysis.status_code==200,analysis.text
    analysed=await execute_task(env,analysis.json()["task_id"])
    assert analysed["task"]["status"]=="completed",analysed
    checked=(await env.client.get(BASE+"/exports/"+analysis.json()["job_id"])).json()
    assert checked["analysis"]["intervals_complete"] and checked["verification"]["source_unchanged"]
    package=await env.client.post(BASE+"/portable",json={"project_id":pid,"expected_revision":project["revision"],"operation_id":op()})
    assert package.status_code==200,package.text
    packed=await execute_task(env,package.json()["task_id"])
    assert packed["task"]["status"]=="completed",packed
    artifact=(await env.client.get(BASE+"/exports/"+package.json()["job_id"])).json()
    assert artifact["verification"]["portable"] and artifact["verification"]["media_count"]==1
    downloaded=await env.client.get(artifact["output_url"])
    assert downloaded.status_code==200 and downloaded.content[:2]==b"PK"
    from bcc.db import resource_reservations as reservations
    async with env.svc.db.session() as session:
        held=(await session.execute(sa.select(reservations.c.id).where(reservations.c.status=="held"))).all()
    assert held==[]

async def test_bad_upload_cleans_temporary_and_preserves_revision(env):
    project=await create(env)
    bad=await env.client.post(BASE+"/media",params={"project_id":project["id"],"filename":"../../secret",
        "expected_revision":0,"operation_id":op()},content=b"x")
    assert bad.status_code==422
    bad=await env.client.post(BASE+"/media",params={"project_id":project["id"],"filename":"evil.mp4",
        "expected_revision":0,"operation_id":op()},content=b"https://private.example/video")
    assert bad.status_code==422
    assert not list((env.svc.video_studio.root/"uploads").glob("*.upload"))
    assert (await env.client.get(BASE+"/projects/"+project["id"])).json()["revision"]==0

async def test_registered_executor_keeps_critical_admission_and_completion(env):
    calls=[]
    async def executor(task,run,engine):
        calls.append(task["id"])
        return "verified deterministic result"
    env.svc.engine.register_executor("test_deterministic",executor)
    async def new():
        from bcc.db import utcnow
        async with env.svc.db.session() as session:
            res=await session.execute(sa.insert(tasks_t).values(prompt="Deterministic QA",title="QA",kind="test_deterministic",
                status="draft",max_retries=0,created_at=utcnow(),updated_at=utcnow()))
            tid=res.inserted_primary_key[0];await session.commit()
        await env.svc.engine.enqueue(tid)
        return tid
    async def deny(task,run):return {"fail":"owner policy denial"}
    env.svc.engine.add_hook("before_run",deny)
    denied=await execute_task(env,await new())
    assert denied["task"]["status"]=="failed" and not calls
    env.svc.engine.hooks["before_run"].remove(deny)
    async def gate(task,run,answer):return {"verdict":"FAIL","requeue":False,"status":"failed"}
    env.svc.engine.add_hook("gate_completion",gate)
    rejected=await execute_task(env,await new())
    assert rejected["task"]["status"]=="failed" and len(calls)==1

async def test_human_lease_blocks_agent_and_invalid_fields_are_not_500(env):
    project=await create(env)
    lease=await env.client.post(BASE+f"/projects/{project['id']}/lease",json={"object_ids":[project["id"]],"ttl_seconds":30})
    assert lease.status_code==200,lease.text
    spec=REGISTRY.get("video.timeline.apply")
    ctx=ToolContext(svc=env.svc,task={"id":900,"meta":{"video_project_id":project["id"]}},run_id=1,agent={})
    with pytest.raises(ValueError):
        await spec.handler({"project_id":project["id"],"expected_revision":0,"operation_id":op(),
                           "command":{"type":"project.rename","name":"Forbidden during human edit"}},ctx)
    bad=await env.client.post(BASE+"/commands",json={"project_id":project["id"],"expected_revision":0,"operation_id":op(),
                       "command":{"type":"clip.move"}})
    assert bad.status_code==422
    bad=await env.client.post(BASE+"/projects",json={"operation_id":"invalid/../id"})
    assert bad.status_code==422

async def test_video_reservation_caps_unknown_memory_and_release(env,monkeypatch):
    from types import SimpleNamespace
    import psutil
    from bcc.features.resources import _video_admit,DEFAULT_POLICY,_after_run
    from bcc.db import resource_reservations as reservations
    project=await create(env)
    pending=[]
    for _ in range(3):
        job=await env.svc.video_studio.export({"project_id":project["id"],"expected_revision":0,"operation_id":op()})
        async with env.svc.db.session() as session:
            task=(await session.execute(sa.select(tasks_t).where(tasks_t.c.id==job["task_id"]))).mappings().one()
            run=(await session.execute(sa.select(runs_t).where(runs_t.c.task_id==job["task_id"]))).mappings().one()
        pending.append((dict(task),dict(run)))
    monkeypatch.setattr(psutil,"virtual_memory",lambda:SimpleNamespace(total=16*1024**3,available=1024**3))
    assert (await _video_admit(env.svc,*pending[0],DEFAULT_POLICY))["defer"]==30
    monkeypatch.setattr(psutil,"virtual_memory",lambda:SimpleNamespace(total=16*1024**3,available=8*1024**3))
    assert await _video_admit(env.svc,*pending[0],DEFAULT_POLICY) is None
    assert await _video_admit(env.svc,*pending[1],DEFAULT_POLICY) is None
    assert (await _video_admit(env.svc,*pending[2],DEFAULT_POLICY))["defer"]==30
    release=await _after_run(env.svc)
    await release(pending[0][0]["id"],pending[0][1]["id"],"stopped")
    assert await _video_admit(env.svc,*pending[2],DEFAULT_POLICY) is None
    def unavailable():raise RuntimeError("no system metric")
    monkeypatch.setattr(psutil,"virtual_memory",unavailable)
    assert "unknown" in (await _video_admit(env.svc,*pending[0],DEFAULT_POLICY))["reason"]

async def test_caption_import_version_comparison_and_asr_blocked(env,monkeypatch):
    project=await create(env)
    response=await env.client.post(BASE+"/captions/import",json={"project_id":project["id"],"expected_revision":0,
        "operation_id":op(),"text":"1\n00:00:00,000 --> 00:00:01,000\nПривет!\n","format":"srt"})
    assert response.status_code==200,response.text
    exported=await env.client.get(BASE+f"/projects/{project['id']}/captions")
    assert "Привет!" in exported.text
    comparison=await env.client.get(BASE+f"/projects/{project['id']}/compare",params={"left":0,"right":1})
    assert "captions" in comparison.json()["changed_fields"]
    monkeypatch.delenv("BOSSMAN_VIDEO_ASR_MODEL",raising=False)
    capabilities=(await env.client.get(BASE+"/capabilities")).json()
    assert capabilities["transcription"]["status"]=="BLOCKED"

async def test_optional_real_trained_proposal_is_queued_pure_and_reviewable(env,tmp_path):
    import os
    if not os.environ.get("BOSSMAN_VIDEO_TRAINED_TOKEN_FILE"):
        pytest.skip("explicit local adapter integration probe not enabled")
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    fixture=tmp_path/"proposal.mp4"
    subprocess.run([shutil.which("ffmpeg"),"-v","error","-f","lavfi","-i","color=c=green:s=160x90:r=25:d=0.4",
                    "-c:v","libx264","-pix_fmt","yuv420p","-y",str(fixture)],check=True,timeout=30)
    project=await create(env)
    uploaded=await env.client.post(BASE+"/media",params={"project_id":project["id"],"filename":fixture.name,
        "expected_revision":0,"operation_id":op()},content=fixture.read_bytes())
    assert uploaded.status_code==200,uploaded.text
    project=uploaded.json()["project"]
    added=await env.client.post(BASE+"/commands",json={"project_id":project["id"],"expected_revision":1,"operation_id":op(),
        "command":{"type":"clip.add","track_id":project["sequences"][0]["tracks"][0]["id"],"clip":{
            "id":"unseen_take_0","media_id":uploaded.json()["media"]["id"],"source_in":0,"source_out":400000,"start":0}}})
    assert added.status_code==200,added.text
    before=added.json()["project"]
    payload={"project_id":project["id"],"expected_revision":before["revision"],"operation_id":op(),
        "objective":"Нужен reverse для unseen_take_0, показывай его с конца.","clip_id":"unseen_take_0"}
    response=await env.client.post(BASE+"/proposals",json=payload)
    assert response.status_code==200,response.text
    job=response.json();assert job["status"]=="queued"
    done=await execute_task(env,job["task_id"])
    assert done["task"]["status"]=="completed",done
    result=(await env.client.get(BASE+"/exports/"+job["job_id"])).json()["proposal"]
    assert result["valid"] and result["applicable"],result
    assert result["command"]=={"type":"clip.reverse","clip_id":"unseen_take_0","reverse":True}
    assert result["usage"]["tokens_out"]>0
    assert (await env.client.get(BASE+"/projects/"+project["id"])).json()==before
    command={"project_id":project["id"],"expected_revision":before["revision"],"operation_id":op(),"command":result["command"]}
    dry=await env.client.post(BASE+"/commands",json={**command,"dry_run":True})
    assert dry.status_code==200,dry.text
    assert (await env.client.get(BASE+"/projects/"+project["id"])).json()==before
    applied=await env.client.post(BASE+"/commands",json=command)
    assert applied.status_code==200,applied.text
    assert applied.json()["project"]["sequences"][0]["tracks"][0]["clips"][0]["reverse"] is True
