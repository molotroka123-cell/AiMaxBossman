"""Motion Studio wiring in the Command Center: status, Epic preview, full render, file check, cancel.

The job lifecycle runs against a stub ``make_video.py`` (a real subprocess and a real ffmpeg/ffprobe
file check); one test drives the real Epic renderer when numpy + Pillow are installed.
"""
from __future__ import annotations

import asyncio
import builtins
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import tracemalloc
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


# --- polling I/O (reproduced 2026-10-08 on 1eac8ac8) -------------------------------------------
# The page polls /status (and a job) every 2 s. Each poll used to settle a finished render inline
# (ffprobe up to 60 s + sha256 of the whole MP4 in one read) ON the event loop, re-read and
# re-parse every */job.json, and read the whole make_video.log to keep its last 2000 characters.


async def test_settling_a_finished_render_never_blocks_the_event_loop_and_runs_once(env, stub_tool, monkeypatch):
    calls = []

    def slow_check(job):   # stands in for ffprobe + hashing a big video
        calls.append(job["id"])
        time.sleep(1.0)
        return {"verified": True, "kind": "preview", "files": [], "reason": ""}

    monkeypatch.setattr(ms, "_check", slow_check)
    r = await env.client.post("/api/motion-studio/jobs", json={"mode": "preview", "spec": SPEC})
    job_id = r.json()["id"]
    await asyncio.wait_for(ms._PROCS[job_id].wait(), timeout=30)   # render ended; nobody settled it yet
    beats = []

    async def heartbeat():
        while True:
            beats.append(time.monotonic())
            await asyncio.sleep(0.02)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    try:
        polls = await asyncio.gather(*(env.client.get(f"/api/motion-studio/jobs/{job_id}") for _ in range(3)),
                                     env.client.get("/api/motion-studio/status"),
                                     env.client.get("/api/motion-studio/jobs"))
    finally:
        beat.cancel()
    worst = max(b - a for a, b in zip(beats, beats[1:]))
    assert worst < 0.5, f"event loop frozen for {worst:.2f} s while a poll settled the render"
    assert calls == [job_id]   # concurrent polls share one settle: one ffprobe, not one per poll
    assert [p.json()["state"] for p in polls[:3]] == ["done"] * 3
    assert polls[3].json()["jobs"][0]["state"] == "done" and polls[4].json()["jobs"][0]["state"] == "done"


def test_file_check_hashes_the_video_in_chunks(tmp_path, monkeypatch):
    block = bytes(range(256)) * 4096   # 1 MiB
    digest = hashlib.sha256()
    with (tmp_path / "video.mp4").open("wb") as fh:
        for _ in range(48):
            fh.write(block)
            digest.update(block)
    probe = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180},
                         {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "2.0"}}
    monkeypatch.setattr(ms.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(ms.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=json.dumps(probe), stderr=""))
    tracemalloc.start()
    try:
        report = ms._check({"mode": "full", "dir": str(tmp_path), "subtitle_lines": 0})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert report["verified"] and report["bytes"] == 48 << 20 and report["sha256"] == digest.hexdigest()
    assert peak < 4 << 20, f"hashing a 48 MiB video allocated {peak / 2**20:.1f} MiB at once"


@pytest.mark.parametrize("repeats", [0, 3, 40000])
def test_log_tail_reads_only_the_end_and_keeps_the_old_text(tmp_path, repeats):
    # make_video.py on Windows: Cyrillic, \r\n newlines, \r progress updates, the odd invalid byte.
    chunk = "кадр 42/9000 — рендер ✓\r\nпрогресс 10%\rпрогресс 20%\n".encode("utf-8") + b"\xff\xc3 bad\n"
    log = tmp_path / "make_video.log"
    with log.open("wb") as fh:
        for _ in range(repeats):
            fh.write(chunk)
        fh.write("финал 🎬 готово\r\n".encode("utf-8")[:-5])   # also ends mid-character
    expected = log.read_text(encoding="utf-8", errors="replace")[-ms.LOG_TAIL:]   # the old contract
    tracemalloc.start()
    try:
        tail = ms._log_tail({"dir": str(tmp_path)})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert tail == expected
    assert peak < 256 << 10, f"a {log.stat().st_size} byte log allocated {peak} bytes for a 2000-char tail"
    assert ms._log_tail({"dir": str(tmp_path / "absent")}) == ""


def _write_job(root: Path, job_id: str, text: str | None = None) -> Path:
    folder = root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "job.json"
    path.write_text(text if text is not None else json.dumps(
        {"id": job_id, "mode": "preview", "state": "done", "error": "", "started": time.time(),
         "finished": time.time(), "style": "epic", "dir": str(folder), "times": [1.0], "source": "custom"}),
        encoding="utf-8")
    return path


async def test_polls_parse_each_job_file_once_and_still_see_other_processes_jobs(env, stub_tool, monkeypatch):
    root = Path(env.settings.data_dir) / "motion-studio"
    for i in range(25):
        _write_job(root, f"old{i:09d}")
    reads = []
    real_open = io.open

    def counting_open(file, mode="r", *args, **kwargs):
        if not isinstance(file, int) and Path(os.fsdecode(file)).name == "job.json" and not set(mode) & set("wax+"):
            reads.append(Path(os.fsdecode(file)).parent.name)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", counting_open)
    monkeypatch.setattr(builtins, "open", counting_open)
    get = env.client.get
    assert len((await get("/api/motion-studio/jobs")).json()["jobs"]) == 20
    assert sorted(reads) == sorted(f"old{i:09d}" for i in range(25))   # first use: everything is read once
    reads.clear()
    await get("/api/motion-studio/status")
    await get("/api/motion-studio/jobs")
    assert (await get("/api/motion-studio/jobs/old000000003")).json()["state"] == "done"
    assert reads == [], f"unchanged job files re-read on every poll: {len(reads)} reads"
    # another process starts a job: the next poll sees it, reading only that file
    _write_job(root, "fromotherpid")
    assert (await get("/api/motion-studio/jobs")).json()["jobs"][0]["id"] == "fromotherpid"
    assert reads == ["fromotherpid"]
    # negative control: a broken file is not re-parsed every poll, but is picked up once it changes
    reads.clear()
    broken = _write_job(root, "brokenjob001", "{")
    await get("/api/motion-studio/jobs")
    await get("/api/motion-studio/jobs")
    assert reads == ["brokenjob001"] and (await get("/api/motion-studio/jobs/brokenjob001")).status_code == 404
    _write_job(root, "brokenjob001")
    later = broken.stat().st_mtime_ns + 2_000_000_000
    os.utime(broken, ns=(later, later))
    assert (await get("/api/motion-studio/jobs/brokenjob001")).json()["state"] == "done"
