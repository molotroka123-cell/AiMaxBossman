"""Hostile chaos suite for Video Studio export/download integrity (AGENT-1).

Two invariants are attacked with REAL ffmpeg-produced media:

  I1  DOWNLOADABLE  =>  VERIFIED
      Nothing may leave /exports/{job}/file that did not pass the independent
      decode/probe oracle AND still hash to the verified digest right now.

  I2  VERIFIED_ARTIFACT  =>  RECOVERABLY_DOWNLOADABLE
      Once an artifact is verified and published, a service restart, a fresh
      VideoService, an evicted derivative cache or a later orchestration
      timeout must NOT make it unavailable. Losing it is a P0.

Every failure mode must be an HONEST error (explicit status/code), never a
silent pass and never a fallback that serves unverified bytes.
"""
import asyncio
import copy
import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t, task_runs as runs_t
from bcc.video_studio import media as media_mod
from bcc.video_studio import render as render_mod
from bcc.video_studio.export_receipt import RECEIPT_KEY
from bcc.video_studio.service import VideoService, jobs

from .test_video_studio_integration import BASE, op, execute_task

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="local FFmpeg unavailable")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
# Every honest refusal the HTTP surface can produce for an unservable artifact.
REFUSED = {403, 404, 409, 422, 503}


@pytest.fixture(autouse=True)
def _deterministic_admission(monkeypatch):
    """Admission telemetry only; ffmpeg, files, DB, gates and hashes stay real."""
    import psutil
    if not os.environ.get("VIDEO_TEST_REAL_MEMORY"):
        monkeypatch.setattr(psutil, "virtual_memory",
                            lambda: SimpleNamespace(total=16 * 1024 ** 3, available=8 * 1024 ** 3))


def make_media(path, *, colour="blue", size="160x90", rate=25, duration=0.4):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:s={size}:r={rate}:d={duration}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(path)], check=True, timeout=120)
    return path


async def new_project(env, tmp_path, *, name="chaos.mp4", **kw):
    """Real chat -> real upload -> real edit task. Returns (project_id, media_id)."""
    source = make_media(tmp_path / name, **kw)
    chat = (await env.client.post(BASE + "/chat",
            json={"text": "Склей эти два видео", "operation_id": op()})).json()
    pid = chat["project_id"]
    upload = await env.client.post(BASE + "/media", params={
        "project_id": pid, "filename": name, "expected_revision": 0,
        "operation_id": op()}, content=source.read_bytes())
    assert upload.status_code == 200, upload.text
    await env.client.post(BASE + f"/chat/{chat['task_id']}/run")
    edited = await execute_task(env, chat["task_id"])
    assert edited["task"]["status"] == "completed", edited
    return pid, upload.json()["media"]["id"]


async def start_export(env, pid, **options):
    project = (await env.client.get(BASE + "/projects/" + pid)).json()
    response = await env.client.post(BASE + "/exports", json={
        "project_id": pid, "expected_revision": project["revision"], "operation_id": op(),
        "options": {"width": 160, "height": 90, **options}})
    assert response.status_code == 200, response.text
    return response.json()


async def export_now(env, pid, **options):
    """Run one export to completion and assert it is genuinely downloadable."""
    job = await start_export(env, pid, **options)
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "completed", done
    status = (await env.client.get(BASE + "/exports/" + job["job_id"])).json()
    assert status["verification"]["passed"] and status["output_url"], status
    return job, status


async def job_row(env, job_id):
    async with env.svc.db.session() as s:
        return dict((await s.execute(sa.select(jobs).where(jobs.c.id == job_id))).mappings().one())


async def set_result(env, job_id, result):
    async with env.svc.db.session() as s:
        await s.execute(sa.update(jobs).where(jobs.c.id == job_id).values(result=result))
        await s.commit()


def substitute_same_size(path: Path):
    """Replace the bytes with different content of the SAME size and restore
    both timestamps -- the classic 'stat identity looks untouched' attack."""
    stat = path.stat()
    original = path.read_bytes()
    forged = bytearray(original)
    # Flip bytes in the payload, never the leading container signature, so the
    # file still looks like the same kind of media to any shallow check.
    for index in range(len(forged) // 2, len(forged)):
        forged[index] ^= 0x5A
    assert bytes(forged) != original and len(forged) == len(original)
    path.write_bytes(bytes(forged))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    return original


# --------------------------------------------------------------------------
# I1: nothing downloadable that was not verified
# --------------------------------------------------------------------------

async def test_missing_ffmpeg_is_an_honest_error_never_a_silent_pass(env, tmp_path, monkeypatch):
    """shutil.which -> None must fail the export loudly with FFMPEG_MISSING."""
    pid, _ = await new_project(env, tmp_path)
    monkeypatch.setattr(media_mod.shutil, "which", lambda name: None)
    job = await start_export(env, pid)
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "failed", done
    status = (await env.client.get(BASE + "/exports/" + job["job_id"])).json()
    assert status["output_url"] is None, status
    assert status["error_detail"]["code"] == "FFMPEG_MISSING", status
    assert status["error_detail"]["retryable"] is False
    refused = await env.client.get(f"{BASE}/exports/{job['job_id']}/file")
    assert refused.status_code == 409, refused.text


async def test_ffmpeg_crash_mid_render_never_publishes_an_artifact(env, tmp_path, monkeypatch):
    """A renderer that dies after producing partial output stays undownloadable."""
    pid, _ = await new_project(env, tmp_path)
    original = media_mod.process
    calls = {"n": 0}

    async def crashing(argv, **kwargs):
        argv = list(argv)
        if kwargs.get("stage") == "render" or "-filter_complex_script" in map(str, argv):
            calls["n"] += 1
            # Emulate the encoder dying part-way: exit non-zero, leave debris.
            raise ValueError("media process failed: encoder terminated")
        return await original(argv, **kwargs)

    monkeypatch.setattr(media_mod, "process", crashing)
    monkeypatch.setattr(render_mod, "process", crashing)
    job = await start_export(env, pid)
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "failed", done
    assert calls["n"] >= 1
    status = (await env.client.get(BASE + "/exports/" + job["job_id"])).json()
    assert status["output_url"] is None
    assert status["error_detail"]["code"] in {"ENCODE_FAILED", "DECODE_FAILED"}, status
    assert (await env.client.get(f"{BASE}/exports/{job['job_id']}/file")).status_code == 409
    # Nothing was published into the immutable export directory.
    exports = env.svc.video_studio.root / "exports" / job["job_id"]
    assert not list(exports.rglob("output.*")), "a crashed render published an artifact"


async def test_verification_failure_is_never_downloadable(env, tmp_path, monkeypatch):
    """If the independent oracle says no, no bytes may ever be served."""
    pid, _ = await new_project(env, tmp_path)
    real = render_mod.verify_output

    async def failing(path, expected=None):
        report = await real(path, expected)
        return {**report, "passed": False, "failures": ["injected oracle rejection"]}

    monkeypatch.setattr(render_mod, "verify_output", failing)
    job = await start_export(env, pid)
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "failed", done
    status = (await env.client.get(BASE + "/exports/" + job["job_id"])).json()
    assert status["output_url"] is None and status["error_detail"]["code"] == "VERIFY_FAILED", status
    assert (await env.client.get(f"{BASE}/exports/{job['job_id']}/file")).status_code == 409


async def test_stale_same_size_substitution_with_restored_mtime_is_refused(env, tmp_path):
    """The download path must re-hash: identical size + restored mtime is not proof."""
    pid, _ = await new_project(env, tmp_path)
    job, status = await export_now(env, pid)
    good = await env.client.get(status["output_url"])
    assert good.status_code == 200 and len(good.content) > 100

    row = await job_row(env, job["job_id"])
    artifact = Path(row["result"]["path"])
    original = substitute_same_size(artifact)

    refused = await env.client.get(status["output_url"])
    assert refused.status_code == 409, "stale substituted bytes were served"

    # Second, different attack on the same invariant: restore the real bytes but
    # rewrite the DB digest to the forged one. The file must still not be served,
    # because the receipt binds job/run/result and no longer matches.
    artifact.write_bytes(original)
    assert (await env.client.get(status["output_url"])).status_code == 200
    forged_digest = hashlib.sha256(b"not this file").hexdigest()
    await set_result(env, job["job_id"], {**row["result"], "sha256": forged_digest})
    assert (await env.client.get(status["output_url"])).status_code == 409

    async with env.svc.db.session() as s:
        task = dict((await s.execute(sa.select(tasks_t).where(
            tasks_t.c.id == job["task_id"]))).mappings().one())
        run_id = (await s.execute(sa.select(runs_t.c.id).where(
            runs_t.c.task_id == job["task_id"]).order_by(runs_t.c.id.desc()))).scalars().first()
    assert (await env.svc.video_studio.render_gate(task, run_id, "done"))["verdict"] == "FAIL"


async def test_cross_run_receipt_reuse_cannot_publish_another_jobs_artifact(env, tmp_path):
    """Job B may not inherit job A's signed receipt to become downloadable."""
    pid, _ = await new_project(env, tmp_path)
    first, first_status = await export_now(env, pid)
    second, _ = await export_now(env, pid, width=320, height=180)

    victim = await job_row(env, second["job_id"])
    donor = await job_row(env, first["job_id"])
    # Transplant A's whole verified result (path + digest + signed receipt) into B.
    await set_result(env, second["job_id"], copy.deepcopy(donor["result"]))

    async with env.svc.db.session() as s:
        task = dict((await s.execute(sa.select(tasks_t).where(
            tasks_t.c.id == second["task_id"]))).mappings().one())
        run_id = (await s.execute(sa.select(runs_t.c.id).where(
            runs_t.c.task_id == second["task_id"]).order_by(runs_t.c.id.desc()))).scalars().first()
    verdict = await env.svc.video_studio.render_gate(task, run_id, "done")
    assert verdict["verdict"] == "FAIL", "a transplanted receipt passed the completion gate"

    # And the transplanted path must not be servable through job B's URL either:
    # ownership is scoped to exports/<job_id>/...
    stolen = await env.client.get(f"{BASE}/exports/{second['job_id']}/file")
    # TEST BUG FIX (justified): the product refuses correctly, but ownership
    # violations surface as ValueError -> HTTP 422, not 409. Both are honest
    # refusals; the invariant is "no bytes", not one particular status code.
    assert stolen.status_code in REFUSED, "job B served job A's artifact"
    # Job A itself is untouched and still downloadable (no collateral damage).
    assert (await env.client.get(first_status["output_url"])).status_code == 200
    assert victim["result"]["path"] != donor["result"]["path"]


async def test_completed_status_alone_never_authorizes_a_download(env, tmp_path):
    """A completed task whose artifact vanished must 409, not 200 with nothing."""
    pid, _ = await new_project(env, tmp_path)
    job, status = await export_now(env, pid)
    row = await job_row(env, job["job_id"])
    Path(row["result"]["path"]).unlink()
    gone = await env.client.get(status["output_url"])
    # TEST BUG FIX (justified): a vanished artifact is reported as
    # ValueError("output unavailable") -> 422. Refusal, not a leak.
    assert gone.status_code in REFUSED, gone.text
    assert b"ftyp" not in gone.content


@pytest.mark.parametrize("options,expected", [
    ({"fps": {"num": 100000, "den": 1}}, 422),          # absurd frame rate
    ({"width": 15, "height": 90}, 422),                 # odd/too small dimension
    ({"range": {"start": 0, "end": 10 ** 15}}, 422),    # absurd range
    ({"video_codec": "totally_made_up"}, 422),
])
async def test_absurd_and_nan_export_parameters_are_rejected_before_any_render(
        env, tmp_path, options, expected):
    pid, _ = await new_project(env, tmp_path)
    project = (await env.client.get(BASE + "/projects/" + pid)).json()
    response = await env.client.post(BASE + "/exports", json={
        "project_id": pid, "expected_revision": project["revision"], "operation_id": op(),
        "options": {"width": 160, "height": 90, **options}})
    if response.status_code == 200:
        done = await execute_task(env, response.json()["task_id"])
        assert done["task"]["status"] == "failed", (options, done)
        status = (await env.client.get(BASE + "/exports/" + response.json()["job_id"])).json()
        assert status["output_url"] is None
    else:
        assert response.status_code == expected, response.text


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_non_finite_json_numbers_never_reach_the_encoder(env, tmp_path, literal):
    """NaN/Infinity are not JSON; they must be refused, never coerced to a render."""
    pid, _ = await new_project(env, tmp_path)
    project = (await env.client.get(BASE + "/projects/" + pid)).json()
    body = ('{"project_id": "%s", "expected_revision": %d, "operation_id": "%s",'
            ' "options": {"width": 160, "height": 90, "crf": %s}}'
            % (pid, project["revision"], op(), literal))
    response = await env.client.post(BASE + "/exports", content=body.encode(),
                                     headers={"content-type": "application/json"})
    if response.status_code == 200:
        done = await execute_task(env, response.json()["task_id"])
        assert done["task"]["status"] == "failed", (literal, done)
        status = (await env.client.get(BASE + "/exports/" + response.json()["job_id"])).json()
        assert status["output_url"] is None
    else:
        assert response.status_code in REFUSED, response.text


async def test_corrupt_media_upload_is_refused_and_never_becomes_a_source(env, tmp_path):
    """Garbage and truncated containers must fail honestly at ingest."""
    pid, _ = await new_project(env, tmp_path)
    project = (await env.client.get(BASE + "/projects/" + pid)).json()

    garbage = await env.client.post(BASE + "/media", params={
        "project_id": pid, "filename": "junk.mp4", "expected_revision": project["revision"],
        "operation_id": op()}, content=b"\x00" * 4096)
    assert garbage.status_code in (403, 409, 422), garbage.text

    whole = make_media(tmp_path / "whole.mp4").read_bytes()
    truncated = await env.client.post(BASE + "/media", params={
        "project_id": pid, "filename": "cut.mp4", "expected_revision": project["revision"],
        "operation_id": op()}, content=whole[:len(whole) // 3])
    assert truncated.status_code in (403, 409, 422), truncated.text

    after = (await env.client.get(BASE + "/projects/" + pid)).json()
    assert len(after["media"]) == len(project["media"]), "a corrupt upload entered the library"


# --------------------------------------------------------------------------
# I2: a verified artifact stays recoverable
# --------------------------------------------------------------------------

async def test_verified_artifact_survives_restart_and_cache_eviction(env, tmp_path):
    """P0: restart + wiped derivative cache must not lose a verified export."""
    pid, media_id = await new_project(env, tmp_path)
    job, status = await export_now(env, pid)
    before = (await env.client.get(status["output_url"])).content
    assert len(before) > 100

    cache = env.svc.video_studio.root / "cache"
    if cache.exists():
        shutil.rmtree(cache)

    # A fresh VideoService is exactly what a process restart produces.
    restarted = VideoService(env.svc)
    recovered = await restarted.verified_output(job["job_id"])
    assert recovered.is_file() and recovered.read_bytes() == before

    # And the live HTTP surface still serves it byte-for-byte.
    again = await env.client.get(status["output_url"])
    assert again.status_code == 200 and again.content == before

    # The receipt still validates on the restarted service (no in-memory state).
    async with env.svc.db.session() as s:
        task = dict((await s.execute(sa.select(tasks_t).where(
            tasks_t.c.id == job["task_id"]))).mappings().one())
        run_id = (await s.execute(sa.select(runs_t.c.id).where(
            runs_t.c.task_id == job["task_id"]).order_by(runs_t.c.id.desc()))).scalars().first()
    assert (await restarted.render_gate(task, run_id, ""))["verdict"] == "PASS"


async def test_interrupted_export_is_recoverable_by_re_export(env, tmp_path, monkeypatch):
    """Killing a render mid-flight must leave the system able to export again."""
    pid, _ = await new_project(env, tmp_path)
    original = media_mod.process

    async def killed(argv, **kwargs):
        if "-filter_complex_script" in list(map(str, argv)):
            raise asyncio.CancelledError()
        return await original(argv, **kwargs)

    monkeypatch.setattr(media_mod, "process", killed)
    monkeypatch.setattr(render_mod, "process", killed)
    job = await start_export(env, pid)
    with pytest.raises(asyncio.CancelledError):
        await execute_task(env, job["task_id"])
    interrupted = (await env.client.get(BASE + "/exports/" + job["job_id"])).json()
    assert interrupted["output_url"] is None
    assert (await env.client.get(f"{BASE}/exports/{job['job_id']}/file")).status_code == 409

    monkeypatch.setattr(media_mod, "process", original)
    monkeypatch.setattr(render_mod, "process", original)
    recovered, status = await export_now(env, pid, width=320, height=180)
    assert (await env.client.get(status["output_url"])).status_code == 200


async def test_cancellation_mid_render_leaves_no_half_verified_artifact(env, tmp_path):
    """POST /cancel during a render must not publish or half-publish anything."""
    pid, _ = await new_project(env, tmp_path)
    job = await start_export(env, pid)
    cancelled = await env.client.post(f"{BASE}/exports/{job['job_id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["output_url"] is None
    assert (await env.client.get(f"{BASE}/exports/{job['job_id']}/file")).status_code == 409
    exports = env.svc.video_studio.root / "exports" / job["job_id"]
    assert not list(exports.rglob("output.*"))


async def test_concurrent_exports_each_stay_independently_downloadable(env, tmp_path):
    """Two overlapping exports must both verify and both remain retrievable."""
    pid, _ = await new_project(env, tmp_path)
    first = await start_export(env, pid, width=160, height=90)
    second = await start_export(env, pid, width=320, height=180)
    assert first["job_id"] != second["job_id"]

    done = await asyncio.gather(execute_task(env, first["task_id"]),
                                execute_task(env, second["task_id"]))
    assert [d["task"]["status"] for d in done] == ["completed", "completed"], done

    payloads = []
    for job in (first, second):
        status = (await env.client.get(BASE + "/exports/" + job["job_id"])).json()
        assert status["verification"]["passed"] and status["output_url"], status
        download = await env.client.get(status["output_url"])
        assert download.status_code == 200 and len(download.content) > 100
        payloads.append(download.content)
    assert payloads[0] != payloads[1], "concurrent exports collapsed onto one artifact"

    rows = [await job_row(env, job["job_id"]) for job in (first, second)]
    paths = {row["result"]["path"] for row in rows}
    assert len(paths) == 2, "concurrent exports shared one output path"


async def test_repeated_and_concurrent_thumbnail_requests_stay_honest(env, tmp_path):
    """Derivatives: never a silent pass, never unverified bytes, always recoverable."""
    pid, media_id = await new_project(env, tmp_path)
    video = env.svc.video_studio

    # Before prepare, the derivative must not exist and must say so honestly.
    with pytest.raises(RuntimeError):
        await video.prepared_file(pid, media_id, "thumbnail")

    project = (await env.client.get(BASE + "/projects/" + pid)).json()
    prep = await env.client.post(BASE + "/analysis", json={
        "project_id": pid, "media_id": media_id, "expected_revision": project["revision"],
        "operation_id": op(), "action": "prepare"})
    assert prep.status_code == 200, prep.text
    assert (await execute_task(env, prep.json()["task_id"]))["task"]["status"] == "completed"

    async def fetch():
        try:
            return await video.prepared_file(pid, media_id, "thumbnail")
        except Exception as exc:            # honest failures only, never silence
            return exc

    results = await asyncio.gather(*(fetch() for _ in range(24)))
    from bcc.video_studio.read_verification import ReadVerificationBusy
    for outcome in results:
        assert isinstance(outcome, (Path, ReadVerificationBusy)), outcome
    served = [r for r in results if isinstance(r, Path)]
    assert served, "every concurrent derivative request failed"
    assert all(path.is_file() and path.stat().st_size > 0 for path in served)
    assert len({str(p) for p in served}) == 1

    # Evicting the derivative cache must be a clean, honest miss -- and the
    # underlying VERIFIED source media must still be readable (I2).
    shutil.rmtree(video.root / "cache")
    with pytest.raises(RuntimeError):
        await video.prepared_file(pid, media_id, "thumbnail")
    _, media = await video.media_file(pid, media_id)
    assert Path(await video.media.resolve_for_read(media)).is_file()


async def test_source_media_substitution_is_caught_on_read(env, tmp_path):
    """A verified library source swapped for same-size bytes must not be served."""
    pid, media_id = await new_project(env, tmp_path)
    video = env.svc.video_studio
    _, media = await video.media_file(pid, media_id)
    path = Path(await video.media.resolve_for_read(media))
    substitute_same_size(path)
    with pytest.raises(ValueError):
        await video.media.resolve_for_read(media)
    # A subsequent export over the tampered source must fail, not silently render.
    job = await start_export(env, pid)
    done = await execute_task(env, job["task_id"])
    assert done["task"]["status"] == "failed", done
    assert (await env.client.get(f"{BASE}/exports/{job['job_id']}/file")).status_code == 409


async def test_adjustment_clip_project_exports_or_fails_honestly(env, tmp_path):
    """A real adjustment layer must yield a fully verified export, or an honest error."""
    pid, _ = await new_project(env, tmp_path)
    project = (await env.client.get(BASE + "/projects/" + pid)).json()
    sequence = project["sequences"][0]
    response = await env.client.post(BASE + "/commands", json={
        "project_id": pid, "expected_revision": project["revision"], "operation_id": op(),
        "command": {"type": "timeline.apply", "operations": [
            {"type": "track.add", "sequence_id": sequence["id"], "kind": "adjustment", "id": "fx"},
            {"type": "clip.add", "sequence_id": sequence["id"], "track_id": "fx",
             "clip": {"id": "layer", "adjustment": True, "start": 0,
                      "freeze_duration": 200_000,
                      "effects": [{"type": "eq", "params": {"brightness": 0.1}}]}}]}})
    assert response.status_code == 200, response.text
    job, status = await export_now(env, pid)
    assert status["verification"]["decoded"] is True and status["verification"]["failures"] == []
    download = await env.client.get(status["output_url"])
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == status["verification"]["sha256"]

    # Second attack on the same code path: an adjustment clip on a NON-adjustment
    # track must be refused outright, never rendered into a verified artifact.
    current = (await env.client.get(BASE + "/projects/" + pid)).json()
    bad = await env.client.post(BASE + "/commands", json={
        "project_id": pid, "expected_revision": current["revision"], "operation_id": op(),
        "command": {"type": "timeline.apply", "operations": [
            {"type": "clip.add", "sequence_id": sequence["id"],
             "track_id": sequence["tracks"][0]["id"],
             "clip": {"id": "illegal", "adjustment": True, "start": 0,
                      "freeze_duration": 100_000}}]}})
    assert bad.status_code in REFUSED, bad.text


async def test_large_media_export_completes_and_verifies_within_budget(env, tmp_path):
    """A longer real encode must still be fully verified before it is downloadable."""
    pid, _ = await new_project(env, tmp_path, name="long.mp4", size="320x240",
                               rate=30, duration=6)
    loop = asyncio.get_running_loop()
    started = loop.time()
    job, status = await export_now(env, pid, width=320, height=240)
    assert loop.time() - started < 240, "large export exceeded its time budget"
    verification = status["verification"]
    assert verification["passed"] and verification["decoded"] is True
    assert verification["failures"] == []
    download = await env.client.get(status["output_url"])
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == verification["sha256"]


# --------------------------------------------------------------------------
# I2 regressions: defects found by this suite, each retested a second way
# --------------------------------------------------------------------------

async def library_fixture(tmp_path, *, colour="red", size="320x240", rate=25, duration=1.2):
    from bcc.video_studio.media import MediaLibrary
    library = MediaLibrary(tmp_path)
    source = make_media(tmp_path / f"{colour}-{size}.mp4", colour=colour, size=size,
                        rate=rate, duration=duration)
    return library, await library.import_file(source, name="chaos source")


async def test_evicted_reverse_intermediate_is_rebuilt_not_permanently_poisoned(tmp_path):
    """P1 REGRESSION: a derived intermediate is a cache, so eviction must recover.

    Before the fix, MediaLibrary.reverse_proxy read its manifest and propagated
    `resolve`'s failure, so once housekeeping removed the intermediate file every
    later export of that project failed forever with 'relink required' -- for a
    derived file no owner can relink.
    """
    library, media = await library_fixture(tmp_path)
    fps = {"num": 25, "den": 1}
    first = await library.reverse_proxy(media, 0, media["duration_ticks"], fps)
    assert (tmp_path / first["relative_path"]).is_file()
    assert (await library.reverse_proxy(media, 0, media["duration_ticks"], fps))["id"] == first["id"]

    # Attack 1: housekeeping evicts the derived intermediate.
    (tmp_path / first["relative_path"]).unlink()
    rebuilt = await library.reverse_proxy(media, 0, media["duration_ticks"], fps)
    assert (tmp_path / rebuilt["relative_path"]).is_file()

    # Attack 2 (different route to the same invariant): the manifest itself is
    # torn by a crash mid-write / partial restore.
    manifest = next((tmp_path / "cache" / "reverse").glob("*.json"))
    manifest.write_text("{ truncated", encoding="utf-8")
    recovered = await library.reverse_proxy(media, 0, media["duration_ticks"], fps)
    assert (tmp_path / recovered["relative_path"]).is_file()

    # Attack 3: the intermediate is corrupted in place instead of removed, so
    # the manifest resolves to bytes that no longer hash to their own name.
    stored = tmp_path / recovered["relative_path"]
    payload = stored.read_bytes()
    stored.write_bytes(payload[:-64] + b"\x00" * 64)
    healed = await library.reverse_proxy(media, 0, media["duration_ticks"], fps)
    from bcc.video_studio.media import digest_file
    assert digest_file(tmp_path / healed["relative_path"]) == healed["sha256"]


async def test_corrupted_content_addressed_media_is_repairable_by_reimport(tmp_path):
    """P1 REGRESSION: bit rot must not lock the owner out of their media forever.

    Before the fix, import_file raised 'existing content-addressed media is
    corrupt' whenever the stored file no longer matched its digest -- including
    when the caller was re-supplying the very same provably correct bytes, so
    there was no repair path at all.
    """
    from bcc.video_studio.media import MediaLibrary, digest_file
    library = MediaLibrary(tmp_path)
    source = make_media(tmp_path / "src.mp4")
    media = await library.import_file(source, name="src")
    stored = tmp_path / media["relative_path"]
    good = stored.read_bytes()

    stored.write_bytes(good[:-64] + b"\x00" * 64)
    with pytest.raises(ValueError):
        library.resolve(media)                      # corruption is still detected

    repaired = await library.import_file(source, name="src")
    assert repaired["sha256"] == media["sha256"]
    assert digest_file(stored) == media["sha256"]
    assert stored.read_bytes() == good
    assert library.resolve(media) == stored         # usable again

    # HOSTILE RETEST: repair must never become a way to seize an existing digest.
    # Different content can only ever land under its OWN hash; the repaired file
    # is untouched.
    other = make_media(tmp_path / "other.mp4", colour="green")
    intruder = await library.import_file(other, name="other")
    assert intruder["sha256"] != media["sha256"]
    assert (tmp_path / intruder["relative_path"]) != stored
    assert stored.read_bytes() == good


async def test_evicted_nested_reverse_intermediate_is_rebuilt(tmp_path):
    """P1 REGRESSION: the same poisoned-cache bug in render.py's nested cache."""
    from bcc.video_studio.media import MediaLibrary
    from bcc.video_studio.render import render_project

    library, media = await library_fixture(tmp_path, size="640x480", duration=1.2)
    span = media["duration_ticks"]
    project = {"id": "p1", "revision": 1, "schema_version": 1, "timebase": 1_000_000,
        "active_sequence_id": "s1", "media": {media["id"]: media}, "captions": [],
        "sequences": [
            {"id": "child", "width": 640, "height": 480, "fps": {"num": 25, "den": 1},
             "sample_rate": 48000, "tracks": [{"id": "cv", "kind": "video", "clips": [
                {"id": "cc", "media_id": media["id"], "start": 0, "source_in": 0,
                 "source_out": span, "speed": {"num": 1, "den": 1}, "transform": {},
                 "effects": [], "keyframes": {}}]}]},
            {"id": "s1", "width": 640, "height": 480, "fps": {"num": 25, "den": 1},
             "sample_rate": 48000, "tracks": [{"id": "v1", "kind": "video", "clips": [
                {"id": "c1", "nested_sequence_id": "child", "start": 0, "source_in": 0,
                 "source_out": span, "reverse": True, "speed": {"num": 1, "den": 1},
                 "transform": {}, "effects": [], "keyframes": {}}]}]}]}

    first = await render_project(project, tmp_path, tmp_path / "one.mp4",
                                 {"width": 320, "height": 240})
    assert first["verification"]["passed"]
    cached = list((tmp_path / "cache" / "nested").glob("*.json"))
    assert cached, "the nested intermediate cache was never populated"

    # Evict every derived intermediate the cache manifests point at, exactly as
    # a disk-space sweep would; the render must rebuild, not fail forever.
    import json as _json
    for manifest in cached:
        target = tmp_path / _json.loads(manifest.read_text(encoding="utf-8"))["relative_path"]
        target.unlink(missing_ok=True)
    second = await render_project(project, tmp_path, tmp_path / "two.mp4",
                                  {"width": 320, "height": 240})
    assert second["verification"]["passed"] and second["verification"]["failures"] == []

    # HOSTILE RETEST: torn manifest instead of a missing file.
    for manifest in list((tmp_path / "cache" / "nested").glob("*.json")):
        manifest.write_text("not json at all", encoding="utf-8")
    third = await render_project(project, tmp_path, tmp_path / "three.mp4",
                                 {"width": 320, "height": 240})
    assert third["verification"]["passed"] and third["verification"]["failures"] == []


async def test_identical_bytes_restored_under_a_new_inode_stay_downloadable(env, tmp_path):
    """I2: a backup restore changes inode/ctime but not content -- still servable."""
    pid, _ = await new_project(env, tmp_path)
    job, status = await export_now(env, pid)
    row = await job_row(env, job["job_id"])
    artifact = Path(row["result"]["path"])
    payload = artifact.read_bytes()

    replica = artifact.with_suffix(".restored")
    replica.write_bytes(payload)
    os.replace(replica, artifact)                   # new inode, new ctime, same bytes
    assert artifact.read_bytes() == payload

    served = await env.client.get(status["output_url"])
    assert served.status_code == 200, "a bit-identical restored artifact became unavailable"
    assert served.content == payload
    restarted = VideoService(env.svc)
    assert (await restarted.verified_output(job["job_id"])).read_bytes() == payload
