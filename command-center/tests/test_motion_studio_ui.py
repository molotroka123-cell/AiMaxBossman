"""Motion Studio wiring in the Command Center: status, Epic preview, full render, file check, cancel.

The job lifecycle runs against a stub ``make_video.py`` (a real subprocess and a real ffmpeg/ffprobe
file check); one test drives the real Epic renderer when numpy + Pillow are installed.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from bcc.features import motion_studio as ms

REPO_TOOL = Path(__file__).resolve().parents[2] / "tools" / "motion_studio"

STUB = textwrap.dedent('''
    import argparse, shutil, subprocess, sys, time
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("spec"); ap.add_argument("--work", type=Path); ap.add_argument("--style")
    ap.add_argument("--no-voice", action="store_true"); ap.add_argument("--tts-models")
    ap.add_argument("--preview", nargs="*", type=float, default=None)
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    text = Path(a.spec).read_text(encoding="utf-8")
    if "SLOW" in text:
        time.sleep(60)
    if "BROKEN" in text:
        sys.exit(3)
    if a.preview:
        for t in a.preview:
            (a.work / f"epic-preview-{t:g}.png").write_bytes(b"\\x89PNG" + b"0" * 4000)
        sys.exit(0)
    (a.work / "soundtrack.wav").write_bytes(b"RIFF" + b"0" * 2000)
    with_audio = "NOAUDIO" not in text
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1:r=10"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(a.work / "video.mp4")]
    subprocess.run(cmd, check=True)
''')

SPEC = {"scenes": [{"type": "title", "start": 0, "end": 2, "vo": [{"t": 0.5, "text": "Привет"}]}]}


@pytest.fixture
def stub_tool(tmp_path, monkeypatch):
    tool = tmp_path / "tool"
    (tool / "examples").mkdir(parents=True)
    (tool / "make_video.py").write_text(STUB, encoding="utf-8")
    (tool / "examples" / "demo.json").write_text('{"scenes": []}', encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_MOTION_STUDIO_DIR", str(tool))
    ms._JOBS.clear()
    ms._PROCS.clear()
    yield tool
    for proc in list(ms._PROCS.values()):
        if proc.returncode is None:
            proc.kill()
    ms._JOBS.clear()
    ms._PROCS.clear()


async def wait_done(client, job_id, timeout=60):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        job = (await client.get(f"/api/motion-studio/jobs/{job_id}")).json()
        if job["state"] != "running":
            return job
        await asyncio.sleep(0.2)
    raise AssertionError("job did not finish")


async def test_status_lists_epic_preset_examples_and_dependencies(env, stub_tool):
    st = (await env.client.get("/api/motion-studio/status")).json()
    assert st["available"] and st["presets"] == ["epic"] and st["examples"] == ["demo"]
    assert set(st["deps"]) >= {"numpy", "pillow", "scipy", "ffmpeg", "ffprobe"} and st["jobs"] == []
    assert st["ready_preview"] is all(st["deps"][k] for k in ("numpy", "pillow", "scipy"))
    assert (await env.client.get("/api/motion-studio/examples/demo")).json() == {"scenes": []}
    assert (await env.client.get("/api/motion-studio/examples/nope")).status_code == 404


async def test_missing_tool_is_reported_not_faked(env, tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_MOTION_STUDIO_DIR", str(tmp_path / "absent"))
    st = (await env.client.get("/api/motion-studio/status")).json()
    assert st["available"] is False and "не найден" in st["why_not"]
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC})
    assert r.status_code == 409


async def test_preview_job_produces_checked_frames(env, stub_tool):
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC, "times": [1, 5]})
    assert r.status_code == 202, r.text
    job = await wait_done(env.client, r.json()["id"])
    assert job["state"] == "done" and job["subtitle_lines"] == 1 and job["style"] == "epic"
    assert {"epic-preview-1.png", "epic-preview-5.png"} <= set(job["outputs"])
    check = (await env.client.get(f"/api/motion-studio/jobs/{job['id']}/check")).json()
    assert check["verified"] is True and check["kind"] == "preview" and len(check["files"]) == 2
    got = await env.client.get(f"/api/motion-studio/jobs/{job['id']}/file", params={"name": "epic-preview-1.png"})
    assert got.status_code == 200 and got.content.startswith(b"\x89PNG")


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not on PATH")
async def test_full_render_is_done_only_after_ffprobe_check(env, stub_tool):
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "full", "spec": SPEC, "no_voice": True})
    job = await wait_done(env.client, r.json()["id"])
    assert job["state"] == "done" and job["music"] is True
    check = (await env.client.get(f"/api/motion-studio/jobs/{job['id']}/check")).json()
    assert check["verified"] and check["video"]["codec"] == "h264" and check["audio"]["codec"] == "aac"
    assert check["duration_s"] > 0 and check["music"] is True and len(check["sha256"]) == 64
    assert (await env.client.get(f"/api/motion-studio/jobs/{job['id']}/file",
                                 params={"name": "video.mp4"})).status_code == 200


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not on PATH")
async def test_a_video_without_audio_is_not_reported_done(env, stub_tool):
    spec = dict(SPEC, note="NOAUDIO")
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "full", "spec": spec})
    job = await wait_done(env.client, r.json()["id"])
    assert job["state"] == "failed" and "звуков" in job["error"]


async def test_failed_process_is_reported_with_log_and_no_success(env, stub_tool):
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": dict(SPEC, note="BROKEN")})
    job = await wait_done(env.client, r.json()["id"])
    assert job["state"] == "failed" and "3" in job["error"]


async def test_one_job_at_a_time_cancel_and_file_whitelist(env, stub_tool):
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": dict(SPEC, note="SLOW")})
    job_id = r.json()["id"]
    again = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC})
    assert again.status_code == 409
    cancelled = (await env.client.post(f"/api/motion-studio/jobs/{job_id}/cancel")).json()
    assert cancelled["state"] == "cancelled"
    for bad in ("../../job.json", "job.json", "make_video.log", "epic-preview-..\\x.png"):
        assert (await env.client.get(f"/api/motion-studio/jobs/{job_id}/file", params={"name": bad})).status_code == 404
    assert (await env.client.get("/api/motion-studio/jobs/nope")).status_code == 404


async def test_cancel_after_a_backend_restart_stops_the_orphaned_render(env, stub_tool):
    # Reproduced 2026-10-08: after a restart the in-memory process handle is gone, the render
    # (a separate process) keeps running and job.json still says «running». Cancel then only
    # relabelled the job «cancelled»: the render kept burning CPU and the one-job guard opened,
    # so a second render could start next to it.
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": dict(SPEC, note="SLOW")})
    job_id = r.json()["id"]
    held = ms._PROCS.pop(job_id)          # what a restart loses: the handle, not the process
    ms._JOBS.clear()
    try:
        assert (await env.client.get(f"/api/motion-studio/jobs/{job_id}")).json()["state"] == "running"
        blocked = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC})
        assert blocked.status_code == 409   # our live render still holds the one-job slot
        cancelled = (await env.client.post(f"/api/motion-studio/jobs/{job_id}/cancel")).json()
        assert cancelled["state"] == "cancelled"
        await asyncio.wait_for(held.wait(), timeout=15)   # the render process really stopped
        assert held.returncode is not None
    finally:
        if held.returncode is None:
            held.kill()
            await held.wait()


async def test_a_reused_pid_is_not_our_render_and_is_never_killed(env, stub_tool):
    # Negative control for the restart path: job.json points at a pid that now belongs to an
    # unrelated program (pid reuse). It must not keep the job «running» forever and must never
    # be killed by cancel.
    stranger = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
    try:
        folder = Path(env.settings.data_dir) / "motion-studio" / "deadbeef0001"
        folder.mkdir(parents=True)
        (folder / "spec.json").write_text("{}", encoding="utf-8")
        ms._save({"id": "deadbeef0001", "mode": "preview", "state": "running", "error": "",
                  "started": time.time(), "finished": None, "style": "epic", "no_voice": True,
                  "pid": stranger.pid, "dir": str(folder), "times": [1.0], "music": False, "source": "custom"})
        ms._JOBS.clear()
        assert ms._orphan({"pid": stranger.pid, "dir": str(folder)}) is None   # alive, but not our render
        job = (await env.client.get("/api/motion-studio/jobs/deadbeef0001")).json()
        assert job["state"] == "interrupted", job
        await env.client.post("/api/motion-studio/jobs/deadbeef0001/cancel")
        assert stranger.returncode is None   # the unrelated program is untouched
        fresh = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC})
        assert fresh.status_code == 202, fresh.text   # a stale record does not block new work
        await wait_done(env.client, fresh.json()["id"])
    finally:
        stranger.kill()
        await stranger.wait()


async def test_input_validation(env, stub_tool):
    post = env.client.post
    assert (await post("/api/motion-studio/jobs", json={"mode": "preview"})).status_code == 422
    assert (await post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC, "example": "demo"})).status_code == 422
    assert (await post("/api/motion-studio/jobs", json={"mode": "preview", "spec": {"x": 1}})).status_code == 422
    assert (await post("/api/motion-studio/jobs", json={"mode": "full", "spec": SPEC, "no_voice": False})).status_code == 422
    assert (await post("/api/motion-studio/jobs", json={"mode": "bogus", "spec": SPEC})).status_code == 422


async def test_endpoints_need_the_owner_session(env, stub_tool):
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        assert (await anon.get("/api/motion-studio/status")).status_code == 401
        assert (await anon.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC})).status_code == 401


async def test_real_epic_preview_when_renderer_dependencies_exist(env):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    pytest.importorskip("scipy")
    ms._JOBS.clear()
    r = await env.client.post("/api/motion-studio/jobs", json={
        "mode": "preview", "example": "bossman_epic_22s", "times": [1.5]})
    assert r.status_code == 202, r.text
    job = await wait_done(env.client, r.json()["id"], timeout=180)
    assert job["state"] == "done", job
    assert (await env.client.get(f"/api/motion-studio/jobs/{job['id']}/check")).json()["verified"] is True


def test_page_is_registered_in_the_ui():
    index = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "index.js").read_text(encoding="utf-8")
    assert "id: 'motion-studio'" in index and (Path(__file__).resolve().parents[1] / "ui" / "pages" / "motion_studio.js").is_file()
    assert (REPO_TOOL / "make_video.py").is_file()
