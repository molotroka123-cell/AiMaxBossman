"""Partial video results in the Studio job layer (owner decision, 2026-09-22).

«Стоп должен обрывать на том, что уже есть». A stop or a blown deadline keeps the segments
the engine really finished, under strict conditions, each of which is a test here:

  * saved only if the bytes passed verification;
  * marked partial=true / complete=false, N of M segments, real duration vs requested,
    and why it stopped;
  * NEVER a successful completion — the job stays cancelled/failed, never "completed",
    and nothing may present it as the full clip;
  * the rest of RT-S5 is untouched: an engine failure, failed verification and an empty
    result still yield no outputs at all.

MOCK_ENGINE throughout (a Python child writing a tiny testsrc clip with ffmpeg); no GPU.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.features.images import process_one
from bcc.studio import runtime as rt
from bcc.studio.providers import sdcpp
from bcc.studio.tables import runs as runs_t

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")

WAN = "sdcpp:wan2.2-ti2v-5b"

# Renders every segment normally until the one named by HANG_AT, which never returns.
# sd.cpp writes a segment's container only when that segment ends, so a hung segment
# leaves no bytes — exactly the real behaviour the salvage path has to cope with.
SEGMENT_ENGINE = r'''
import os, re, subprocess, sys, time, shutil
out = sys.argv[sys.argv.index("-o") + 1]
hang_at = int(os.environ.get("MOCK_HANG_AT", "99"))
m = re.search(r"-s(\d+)\.webm$", out)
index = int(m.group(1)) if m else 0
if index >= hang_at:
    print("MOCK_ENGINE hanging on segment", index, flush=True)
    marker = os.environ.get("MOCK_HANG_MARKER")
    if marker:
        open(marker, "w").write(str(index))
    time.sleep(600)
w = sys.argv[sys.argv.index("-W") + 1]; h = sys.argv[sys.argv.index("-H") + 1]
ffmpeg = shutil.which("ffmpeg")
if out.endswith(".webm"):
    # the frames that were asked for, like the real engine (a wrong length is refused)
    fps = sys.argv[sys.argv.index("--fps") + 1] if "--fps" in sys.argv else "16"
    frames = sys.argv[sys.argv.index("--video-frames") + 1] if "--video-frames" in sys.argv else "16"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate={fps}",
                    "-frames:v", frames, "-c:v", "libvpx", out], check=True)
else:
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}",
                    "-frames:v", "1", out], check=True)
print("MOCK_ENGINE segment", index, "done")
'''


@pytest.fixture
def engine(tmp_path, monkeypatch):
    models = tmp_path / "media"
    files = {}
    for name in ("wan2.2-ti2v-5b", "z-image-turbo"):
        files[name] = {}
        for role in ("diffusion", "vae", "text_encoder"):
            p = models / name / f"{role}.bin"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(role.encode())
            files[name][role] = {"path": f"{name}/{role}.bin", "bytes": p.stat().st_size,
                                 "sha256": hashlib.sha256(role.encode()).hexdigest()}
    (models / "MANIFEST.json").write_text(json.dumps(
        {"schema_version": 1, "engine": {"release": "MOCK_ENGINE"},
         "engines": {k: {"files": v} for k, v in files.items()}}), encoding="utf-8")
    script = tmp_path / "segment_engine.py"
    script.write_text(SEGMENT_ENGINE, encoding="utf-8")
    monkeypatch.setenv(sdcpp.BIN_ENV, sys.executable)
    monkeypatch.setenv(sdcpp.MODELS_ENV, str(models))
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MOCK_HANG_AT", "99")
    real_argv = sdcpp._argv

    def fake_argv(cfg, model_id, plane, settings, files_, out, init):
        argv = real_argv(cfg, model_id, plane, settings, files_, out, init)
        return [sys.executable, str(script), *argv[1:]]

    monkeypatch.setattr(sdcpp, "_argv", fake_argv)
    monkeypatch.setattr(sdcpp.SdCppProvider, "fake", True, raising=False)
    return monkeypatch


async def _job(env, **settings):
    r = await env.client.post("/api/studio/jobs", json={"model": WAN, "prompt": "волны на закате",
                                                        "settings": settings})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _runs(env, deleted=False):
    r = await env.client.get(f"/api/studio/runs?deleted={'true' if deleted else 'false'}")
    return r.json()["items"]


async def _wait_for_run(env, timeout=60):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        items = await _runs(env)
        if items:
            return items[0]
        await asyncio.sleep(0.1)
    return None


async def _cancel_after_segment(env, engine, jid, hang_at):
    """Run the job with the chain hanging at `hang_at`, then cancel it as the owner would."""
    engine.setenv("MOCK_HANG_AT", str(hang_at))
    # Cancel once the engine is actually hanging on segment `hang_at` (so every earlier segment
    # has finished), not after a fixed sleep: the mock now renders the 81 frames it is asked for,
    # which on a slow host took longer than the old fixed 6 s.
    marker = Path(env.settings.data_dir) / "mock-hang.marker"
    engine.setenv("MOCK_HANG_MARKER", str(marker))
    worker = asyncio.create_task(process_one(env.svc))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 120
    while not marker.is_file() and loop.time() < deadline and not worker.done():
        await asyncio.sleep(0.1)
    assert marker.is_file(), "the mock engine never reached the hanging segment"
    await asyncio.sleep(0.5)                           # the provider has recorded the spawn
    assert (await env.client.post(f"/api/studio/jobs/{jid}/cancel")).status_code == 200
    await asyncio.wait_for(worker, 60)


# --------------------------------------------------------------------- end to end

async def test_owner_stop_keeps_the_finished_part_marked_incomplete(env, engine):
    """1 of 3 segments finished, the owner presses stop: the part is saved and labelled."""
    jid = await _job(env, length="15s", width=640, height=352, steps=16, seed=3)
    await _cancel_after_segment(env, engine, jid, hang_at=1)

    run = await _wait_for_run(env)
    assert run is not None, "the finished segment was thrown away by the stop"
    prov = run["provenance"]
    assert prov["partial"] is True and prov["complete"] is False
    detail = prov["partial_detail"]
    assert detail["segments_done"] == 1 and detail["segments_total"] == 3
    assert detail["stopped_by"] == "canceled" and detail["reason"] == "canceled"
    assert detail["duration_s"] < detail["duration_s_if_complete"]
    assert detail["duration_s"] == pytest.approx(81 / 16, abs=1e-3)
    assert detail["duration_s_if_complete"] == pytest.approx((81 + 80 * 2) / 16, abs=1e-3)
    # the bytes really are a playable video: verify_file ran before the row was written
    assert run["mime"] == "video/mp4" and run["file_bytes"] > 0 and len(run["sha256"]) == 64
    assert Path(run["provenance"]["output"]["path"]).is_file()

    # the JOB is not a success, in any wording
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "cancelled" and job["status"] != "completed"
    # and the trace says the same, loudly
    trace = prov["settings_resolved"]["engine_trace"]
    assert trace["partial"] is True and trace["complete"] is False
    assert trace["generation"]["source"] == "engine_process_interrupted"
    assert "no frame was repeated" in trace["generation"]["synthesis"]


async def test_owner_stop_before_any_segment_saves_nothing(env, engine):
    """Zero segments finished: there is nothing to save, and nothing is invented."""
    jid = await _job(env, length="15s", width=640, height=352, steps=16, seed=4)
    await _cancel_after_segment(env, engine, jid, hang_at=0)

    await asyncio.sleep(1.0)
    assert await _runs(env) == [], "an empty or invented result was saved"
    assert await _runs(env, deleted=True) == []
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "cancelled"


async def test_a_partial_never_satisfies_the_completion_gate(env, engine):
    """The job's own completion check must not accept a partial as the requested output."""
    jid = await _job(env, length="10s", width=640, height=352, steps=16, seed=5)
    await _cancel_after_segment(env, engine, jid, hang_at=1)
    run = await _wait_for_run(env)
    assert run is not None
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] != "completed"
    assert job["studio"]["verdict"] != "PASS"
    assert run["provenance"]["partial"] is True


# --------------------------------------------------------------------- RT-S5 matrix

async def _partial_run(env, jid, path, *, partial):
    plane = (await env.client.get(f"/api/studio/jobs/{jid}")).json()["studio"]["plane"]
    model = next(m for m in rt.model_specs() if m["id"] == WAN)
    return await rt.persist(env.svc, jid, plane, model, path, cost=0, partial=partial)


@pytest.mark.parametrize("reason,kept", [("canceled", True), ("timeout", True), ("budget", True),
                                         ("malformed", False), ("provider_down", False),
                                         ("interrupted_unknown", False)])
async def test_fail_keeps_only_marked_partials_and_only_for_a_stop(env, engine, reason, kept):
    """The RT-S5 exception, one reason at a time. A stop/deadline/budget spares a MARKED
    partial; an engine failure spares nothing — that rule is not weakened."""
    jid = await _job(env, length="10s", width=640, height=352, steps=16, seed=6)
    await _cancel_after_segment(env, engine, jid, hang_at=1)
    run = await _wait_for_run(env)
    assert run is not None and run["provenance"]["partial"] is True
    path = Path(run["provenance"]["output"]["path"])

    async with env.svc.db.session() as s:
        await s.execute(sa.update(rt.image_jobs).where(rt.image_jobs.c.id == jid)
                        .values(status="running"))
        await s.commit()
    await rt.fail(env.svc, jid, reason, f"forced {reason}")

    survivors = [r["id"] for r in await _runs(env)]
    assert (run["id"] in survivors) is kept, (reason, survivors)
    assert path.is_file() is kept, f"{reason}: file on disk disagrees with the row"


async def test_an_unmarked_output_of_a_stopped_job_is_still_trashed(env, engine):
    """Negative control for the exception itself: the spare is for MARKED partials only.
    An ordinary (unmarked) output of a job that was stopped is still an unfinished job's
    output, and RT-S5 still removes it."""
    jid = await _job(env, width=640, height=352, frames=17, steps=16, seed=8)
    assert await process_one(env.svc) == jid
    run = (await _runs(env))[0]
    assert "partial" not in run["provenance"]
    path = Path(run["provenance"]["output"]["path"])

    async with env.svc.db.session() as s:
        await s.execute(sa.update(rt.image_jobs).where(rt.image_jobs.c.id == jid)
                        .values(status="running"))
        await s.commit()
    await rt.fail(env.svc, jid, "canceled", "owner stopped it")
    assert await _runs(env) == [], "an unmarked output survived a stop"
    assert not path.exists()


async def test_corrupt_bytes_are_never_saved_as_a_partial(env, engine, tmp_path):
    """The verification gate applies to a partial exactly as to a full result: bytes that do
    not probe and decode produce no row and no file, whatever the stop reason was."""
    jid = await _job(env, length="10s", width=640, height=352, steps=16, seed=9)
    broken = Path(rt.storage(env.svc).root) / f"generated-{jid}-broken.mp4"
    broken.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"MOCK garbage" * 64)
    with pytest.raises((ValueError, OSError, KeyError)):
        await _partial_run(env, jid, broken,
                           partial={"segments_done": 1, "segments_total": 2, "stopped_by": "canceled"})
    assert await _runs(env) == []


def test_is_partial_run_requires_both_flags_explicitly():
    """Nothing is inferred: a full output can never drift into being spared."""
    assert rt.is_partial_run({"partial": True, "complete": False}) is True
    for bogus in ({}, None, "partial", {"partial": True}, {"complete": False},
                  {"partial": True, "complete": True}, {"partial": "yes", "complete": False},
                  {"partial": 1, "complete": 0}):
        assert rt.is_partial_run(bogus) is False, bogus
