"""Authenticated analysis/interchange routes and canonical task negative paths."""
import os
from datetime import timedelta
from types import SimpleNamespace
import pytest
import sqlalchemy as sa
from bcc.db import tasks as tasks_t,task_runs as runs_t,utcnow
from bcc.tools import REGISTRY,ToolContext
from .conftest import FakeAdapter
from .helpers import make_stack
from .test_video_studio_integration import BASE,create,execute_task,op
from .test_video_studio_skill_scenarios import media_sources

async def uploaded(env,media_sources):
    p=await create(env)
    response=await env.client.post(BASE+"/media",params={"project_id":p["id"],"filename":"fixture.mp4",
        "expected_revision":0,"operation_id":op()},content=media_sources[0].read_bytes())
    assert response.status_code==200,response.text
    return response.json()["project"],response.json()["media"]

def admission(monkeypatch):
    if not os.environ.get("VIDEO_TEST_REAL_MEMORY"):
        import psutil
        monkeypatch.setattr(psutil,"virtual_memory",lambda:SimpleNamespace(total=16*1024**3,available=8*1024**3))

async def test_real_cached_prepare_scopes_probe_and_interchange(env,media_sources,monkeypatch):
    admission(monkeypatch)
    p,m=await uploaded(env,media_sources)
    pid=p["id"]
    assert (await env.client.get(BASE+f"/media/{m['id']}/thumbnail",params={"project_id":pid})).status_code==409
    for action in ("prepare","scope","hardware_probe"):
        response=await env.client.post(BASE+"/analysis",json={"project_id":pid,"media_id":m["id"],
            "expected_revision":p["revision"],"operation_id":op(),"action":action,"width":160,"height":90})
        assert response.status_code==200,response.text
        value=response.json()
        await execute_task(env,value["task_id"])
        job=(await env.client.get(BASE+"/exports/"+value["job_id"])).json()
        assert job["status"]=="completed",job
        if action=="scope":
            assert job["analysis"]["snapshot"] and not job["analysis"]["live"]
            output=await env.client.get(job["output_url"])
            assert output.headers["content-type"]=="image/png" and output.content.startswith(b"\x89PNG")
        if action=="hardware_probe":
            assert job["analysis"]["available"] and "actual frames" in job["analysis"]["evidence"]
    for kind in ("thumbnail","proxy","waveform"):
        response=await env.client.get(BASE+f"/media/{m['id']}/{kind}",params={"project_id":pid})
        assert response.status_code==200 and len(response.content)>100,response.text[:100]
    response=await env.client.post(BASE+"/commands",json={"project_id":pid,"expected_revision":p["revision"],"operation_id":op(),
        "command":{"type":"clip.add","track_id":p["sequences"][0]["tracks"][0]["id"],"clip":{"id":"shotcut-clip","media_id":m["id"],"start":0,"source_out":2_000_000}}})
    assert response.status_code==200,response.text
    p=response.json()["project"]
    ctx=ToolContext(svc=env.svc,task={"id":991,"meta":{"video_project_id":pid}},run_id=0,agent={})
    result=await REGISTRY.get("video.project.interchange").handler({"project_id":pid,"format":"shotcut"},ctx)
    assert not result.error and not result.data["parity_claim"]
    xml=await env.client.get(result.data["download_url"])
    assert xml.status_code==200 and b"<mlt" in xml.content
    assert (await env.svc.video_studio.store.get(pid))["revision"]==p["revision"]

async def test_cancel_and_expired_video_job_never_replay(env):
    p=await create(env)
    async def queued():
        response=await env.client.post(BASE+"/exports",json={"project_id":p["id"],"expected_revision":0,"operation_id":op()})
        assert response.status_code==200,response.text
        return response.json()
    cancelled=await queued()
    response=await env.client.post(BASE+f"/exports/{cancelled['job_id']}/cancel")
    assert response.json()["status"]=="stopped"
    stale=await queued()
    async with env.svc.db.session() as session:
        await session.execute(sa.update(runs_t).where(runs_t.c.task_id==stale["task_id"]).values(
            status="running",worker_lease_until=utcnow()-timedelta(seconds=60)))
        await session.execute(sa.update(tasks_t).where(tasks_t.c.id==stale["task_id"]).values(status="running"))
        await session.commit()
    assert await env.svc.engine.recover()==1
    assert await env.svc.engine.recover()==0
    status=(await env.client.get(BASE+f"/exports/{stale['job_id']}")).json()
    assert status["status"]=="failed" and status["output_url"] is None
    async with env.svc.db.session() as session:
        count=(await session.execute(sa.select(sa.func.count()).select_from(runs_t).where(runs_t.c.task_id==stale["task_id"]))).scalar_one()
    assert count==1
    assert not list((env.svc.video_studio.root/"exports").glob("**/*.*"))

async def test_portable_roundtrip_and_unexpected_zip_member_denied(env,media_sources,monkeypatch):
    import io,zipfile
    admission(monkeypatch)
    p,m=await uploaded(env,media_sources)
    response=await env.client.post(BASE+"/portable",json={"project_id":p["id"],"expected_revision":p["revision"],"operation_id":op()})
    value=response.json();await execute_task(env,value["task_id"])
    archive=(await env.client.get(BASE+f"/exports/{value['job_id']}/file")).content
    assert archive.startswith(b"PK")
    target=await create(env)
    query={"project_id":target["id"],"expected_revision":0,"operation_id":op()}
    response=await env.client.post(BASE+"/portable/import",params=query,content=archive)
    assert response.status_code==200,response.text
    value=response.json();task=await execute_task(env,value["task_id"])
    assert task["task"]["status"]=="completed",task
    imported=await env.svc.video_studio.store.get(target["id"])
    assert imported["id"]==target["id"] and imported["revision"]==1
    assert imported["media"][m["id"]]["sha256"]==m["sha256"]
    assert (await env.client.post(BASE+"/portable/import",params=query,content=archive)).json()["job_id"]==value["job_id"]
    evil=io.BytesIO(archive)
    with zipfile.ZipFile(evil,"a") as zip:zip.writestr("../../outside.txt","escape")
    response=await env.client.post(BASE+"/portable/import",params={**query,"expected_revision":1,"operation_id":op()},content=evil.getvalue())
    assert response.status_code==200,response.text
    task=await execute_task(env,response.json()["task_id"])
    assert task["task"]["status"]=="failed"
    assert (await env.svc.video_studio.store.get(target["id"]))==imported
    assert not (env.settings.data_dir/"outside.txt").exists()

async def test_queued_tracking_preserves_integer_time_contract(env,media_sources,monkeypatch):
    admission(monkeypatch)
    p,m=await uploaded(env,media_sources)
    captured={}
    async def track(path,box,*,start,end):
        assert type(start) is int and type(end) is int
        captured.update(start=start,end=end)
        return {"points":[],"method":"contract-test","limits":"no visual tracking claimed"}
    monkeypatch.setattr("bcc.video_studio.analysis.track_object",track)
    response=await env.client.post(BASE+"/analysis",json={"project_id":p["id"],"media_id":m["id"],"expected_revision":p["revision"],
        "operation_id":op(),"action":"track","box":[10,10,40,40],"source_in":100000,"source_out":1000000})
    assert response.status_code==200,response.text
    task=await execute_task(env,response.json()["task_id"])
    assert task["task"]["status"]=="completed",task
    assert captured=={"start":100000,"end":1000000}

async def test_nonvideo_question_uses_existing_agent_without_project(env):
    fake=FakeAdapter("Монтаж соединяет кадры в последовательность.")
    env.svc.registry.adapter_factory=lambda m,p:fake
    stack=await make_stack(env.client)
    text="Как монтировать видео?"
    route=await env.client.post(BASE+"/chat",json={"text":text,"operation_id":op()})
    assert route.json()=={"handled":False}
    response=await env.client.post("/api/tasks",json={"title":text,"prompt":text,"agent_id":stack["agent"]["id"],"run_now":True})
    assert response.status_code==200,response.text
    task=await execute_task(env,response.json()["task"]["id"])
    assert task["task"]["status"]=="completed" and fake.calls==1
    assert not (await env.client.get(BASE+"/projects")).json()["projects"]

@pytest.mark.skipif(not os.environ.get("VIDEO_TEST_LOCAL_TRANSLATION"),reason="explicit local translation integration flag required")
async def test_real_translation_job_is_reviewed_draft(env,media_sources,monkeypatch):
    admission(monkeypatch)
    p,m=await uploaded(env,media_sources)
    cues=[{"id":"owner-text","start":0,"end":1_000_000,"text":"Hello, welcome to our project."}]
    response=await env.client.post(BASE+"/commands",json={"project_id":p["id"],"expected_revision":p["revision"],
        "operation_id":op(),"command":{"type":"captions.replace","captions":cues}})
    assert response.status_code==200,response.text
    p=response.json()["project"]
    response=await env.client.post(BASE+"/analysis",json={"project_id":p["id"],"media_id":m["id"],"expected_revision":p["revision"],
        "operation_id":op(),"action":"translate","source":"en","target":"ru"})
    assert response.status_code==200,response.text
    await execute_task(env,response.json()["task_id"])
    job=(await env.client.get(BASE+"/exports/"+response.json()["job_id"])).json()
    assert job["status"]=="completed",job
    draft=job["analysis"]
    assert draft["draft_only"] and draft["local"]
    assert draft["captions"][0]["id"]==cues[0]["id"]
    assert any("а"<=c.lower()<="я" for c in draft["captions"][0]["text"])
    assert (await env.svc.video_studio.store.get(p["id"]))==p
