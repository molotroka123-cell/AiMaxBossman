"""Twelve executable skill workflows: real files/FFmpeg/SQLite/shared ToolSpecs.

Provider reasoning is not evaluated here. Calls represent already-authorized tools;
separate policy tests verify permission decisions. No cloud/model calls are made.
"""
import os
import shutil
import subprocess
from types import SimpleNamespace
import pytest
from bcc.tools import REGISTRY, ToolContext
from .test_video_studio_integration import BASE, create, execute_task, op

SCENARIOS=("stitch_two","stream_copy","normalize_incompatible","vertical_reels",
    "observe_silence","speech_denoise","music_ducking","russian_captions",
    "fresh_vibes_30_seconds","replace_scene","failed_export","missing_generation")

@pytest.fixture
def media_sources(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("real FFmpeg required")
    paths=[]
    for index,(color,size,fps) in enumerate((("red","160x90",25),("blue","160x90",25),("green","96x64",30))):
        path=tmp_path/f"source-{index}.mp4"
        subprocess.run([shutil.which("ffmpeg"),"-v","error","-f","lavfi","-i",f"color=c={color}:s={size}:r={fps}:d=2",
            "-f","lavfi","-i",r"aevalsrc=if(between(t\,0.5\,1.5)\,0\,0.1*sin(440*2*PI*t)):s=48000:d=2",
            "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest","-y",str(path)],check=True,timeout=30)
        paths.append(path)
    return paths

@pytest.mark.parametrize("scenario",SCENARIOS)
async def test_executable_skill_workflow(env,media_sources,monkeypatch,scenario):
    if not os.environ.get("VIDEO_TEST_REAL_MEMORY"):
        import psutil
        monkeypatch.setattr(psutil,"virtual_memory",lambda:SimpleNamespace(total=16*1024**3,available=8*1024**3))
    project=await create(env)
    pid=project["id"]
    indices=(0,2) if scenario=="normalize_incompatible" else (0,1)
    media=[]
    for index in indices:
        response=await env.client.post(BASE+"/media",params={"project_id":pid,"filename":media_sources[index].name,
            "expected_revision":project["revision"],"operation_id":op()},content=media_sources[index].read_bytes())
        assert response.status_code==200,response.text
        project=response.json()["project"];media.append(response.json()["media"])
    ctx=ToolContext(svc=env.svc,task={"id":991,"meta":{"video_project_id":pid}},run_id=0,
        agent={"tools":[],"permissions":{}})

    async def tool(name,args):
        result=await REGISTRY.get("video."+name).handler(args,ctx)
        assert not result.error,result.content
        return result.data

    async def command(command,name="timeline.apply"):
        nonlocal project
        payload={"project_id":pid,"expected_revision":project["revision"],"operation_id":op(),"command":command}
        dry=await tool(name,{**payload,"dry_run":True})
        assert (await env.svc.video_studio.store.get(pid))["revision"]==project["revision"]
        result=await tool(name,payload);project=result["project"]
        assert dry["project"]["revision"]+1==project["revision"]

    async def run_job(value):
        task=await execute_task(env,value["task_id"])
        job=(await env.client.get(BASE+"/exports/"+value["job_id"])).json()
        return task,job

    video=project["sequences"][0]["tracks"][0]["id"]
    audio=project["sequences"][0]["tracks"][1]["id"]
    two=scenario in ("stitch_two","stream_copy","normalize_incompatible")
    await command({"type":"timeline.apply","operations":[{"type":"clip.add","track_id":video,"clip":{
        "id":f"take-{i}","media_id":item["id"],"start":i*2_000_000,"source_in":0,"source_out":2_000_000}}
        for i,item in enumerate(media if two else media[:1])]})
    options={"width":160,"height":90,"fps":{"num":25,"den":1}}
    expected_duration=4_000_000 if two else 2_000_000
    if scenario=="stream_copy":
        await command({"type":"sequence.update","patch":{"width":160,"height":90,"fps":{"num":25,"den":1}}})
        options={"mode":"stream_copy"}
    elif scenario=="vertical_reels":
        options.update(width=720,height=1280)
    elif scenario=="observe_silence":
        _,job=await run_job(await tool("media.analyse",{"project_id":pid,"media_id":media[0]["id"],
            "expected_revision":project["revision"],"operation_id":op(),"action":"silence_ranges"}))
        assert job["status"]=="completed",job
        ranges=job["analysis"]["keep_ranges"]
        assert len(ranges)==2,ranges
        # Use only measured boundaries, preserving each source offset.
        await command({"type":"clip.trim","clip_id":"take-0","source_in":ranges[0]["start"],"source_out":ranges[0]["end"]})
        expected_duration=ranges[0]["end"]-ranges[0]["start"]
    elif scenario=="speech_denoise":
        await command({"clip_id":"take-0","effect":{"type":"denoise","params":{"reduction":12}}},"audio.process")
    elif scenario=="music_ducking":
        await command({"type":"clip.add","track_id":audio,"clip":{"id":"music","media_id":media[1]["id"],"start":0,"source_in":0,"source_out":2_000_000}})
        await command({"clip_id":"music","effect":{"type":"ducking","params":{"sidechain_track_id":video}}},"audio.process")
    elif scenario=="russian_captions":
        await command({"captions":[{"id":"supplied-transcript","start":0,"end":1_000_000,"text":"Привет, мир!"}]},"captions.edit")
        assert "Привет" in (await env.client.get(BASE+f"/projects/{pid}/captions")).text
    elif scenario=="fresh_vibes_30_seconds":
        await command({"type":"title.add","track_id":video,"start":2_000_000,"duration":28_000_000,"title":{"text":"Fresh Vibes"}})
        expected_duration=30_000_000
    elif scenario=="replace_scene":
        before=project["sequences"]
        changed=await tool("media.relink",{"project_id":pid,"media_id":media[0]["id"],"replacement_media_id":media[1]["id"],
            "expected_revision":project["revision"],"operation_id":op()})
        project=changed["project"]
        assert project["sequences"]==before
        assert project["media"][media[0]["id"]]["sha256"]==media[1]["sha256"]
    elif scenario=="failed_export":
        source=env.svc.video_studio.media.resolve(media[0])
        with source.open("ab") as stream:stream.write(b"external change")
    elif scenario=="missing_generation":
        monkeypatch.delenv("BOSSMAN_VIDEO_ASR_MODEL",raising=False)
        caps=(await env.client.get(BASE+"/capabilities")).json()
        assert caps["generation"]["status"]=="BLOCKED"
        assert caps["transcription"]["status"]=="BLOCKED"
    job_request={"project_id":pid,"expected_revision":project["revision"],"operation_id":op(),"options":options}
    task,job=await run_job(await tool("export.start",job_request))
    if scenario=="failed_export":
        assert job["status"]=="failed" and not job.get("output_url"),job
        assert task["task"]["max_retries"]==0
        assert (await env.client.get(BASE+f"/exports/{job['job_id']}/file")).status_code==409
        return
    assert job["status"]=="completed",(task,job)
    assert abs(job["verification"]["duration_ticks"]-expected_duration)<=100_000
    assert job["verification"]["has_audio"]
    if scenario=="vertical_reels":
        assert (job["verification"]["width"],job["verification"]["height"])==(720,1280)
    await tool("output.verify",{"job_id":job["job_id"]})
    downloaded=await env.client.get(job["output_url"])
    assert downloaded.status_code==200 and len(downloaded.content)>1000
