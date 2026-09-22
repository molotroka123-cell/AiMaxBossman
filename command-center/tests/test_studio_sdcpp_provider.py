"""Local stable-diffusion.cpp provider through the real Studio queue.

The engine here is a MOCK_ENGINE marked `SdCppProvider.fake = True`: a Python child
that writes a small test video, so the queue/verification/cancel contract runs in CI
without model weights. A mock run never writes the "verified" probe and is never
evidence of real generation. Red-team cases live in test_studio_sdcpp_hostile.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from bcc.features.images import process_one
from bcc.studio.providers import sdcpp

FAKE_ENGINE = r'''
import subprocess, sys, time, shutil
out = sys.argv[sys.argv.index("-o") + 1]
mode = sys.argv[1]
if mode == "slow":
    time.sleep(60)
if mode == "fail":
    print("fake engine: model load failed"); sys.exit(3)
if mode == "corrupt":
    open(out, "wb").write(b"\x1aE\xdf\xa3" + b"MOCK_ENGINE garbage" * 64); sys.exit(0)
w = sys.argv[sys.argv.index("-W") + 1]; h = sys.argv[sys.argv.index("-H") + 1]
ffmpeg = shutil.which("ffmpeg")
if out.endswith(".webm"):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=16",
                    "-t", "1", "-c:v", "libvpx", out], check=True)
else:
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}",
                    "-frames:v", "1", out], check=True)
print("fake engine: done")
'''

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    models = tmp_path / "media"
    files = {}
    for engine_name, roles in (("wan2.2-ti2v-5b", ("diffusion", "vae", "text_encoder")),
                               ("z-image-turbo", ("diffusion", "vae", "text_encoder"))):
        files[engine_name] = {}
        for role in roles:
            p = models / engine_name / f"{role}.bin"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(role.encode())
            files[engine_name][role] = {"path": f"{engine_name}/{role}.bin", "bytes": p.stat().st_size,
                                        "sha256": hashlib.sha256(role.encode()).hexdigest()}
    (models / "MANIFEST.json").write_text(json.dumps(
        {"schema_version": 1, "engine": {"release": "MOCK_ENGINE"},
         "engines": {k: {"files": v} for k, v in files.items()}}),
        encoding="utf-8")
    script = tmp_path / "fake_engine.py"
    script.write_text(FAKE_ENGINE, encoding="utf-8")
    monkeypatch.setenv(sdcpp.BIN_ENV, sys.executable)
    monkeypatch.setenv(sdcpp.MODELS_ENV, str(models))
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "data"))  # hash cache lands in the test tree
    mode = {"value": "ok"}
    real_argv = sdcpp._argv

    def fake_argv(cfg, model_id, plane, settings, files_, out, init):
        argv = real_argv(cfg, model_id, plane, settings, files_, out, init)
        return [sys.executable, str(script), mode["value"], *argv[1:]]

    monkeypatch.setattr(sdcpp, "_argv", fake_argv)
    monkeypatch.setattr(sdcpp.SdCppProvider, "fake", True, raising=False)
    return mode


async def _job(env, model, **settings):
    r = await env.client.post("/api/studio/jobs", json={"model": model, "prompt": "кот на крыше",
                                                        "settings": settings})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_video_job_verified_with_engine_trace(env, engine):
    models = {m["id"]: m for m in (await env.client.get("/api/studio/models")).json()["items"]}
    assert models["sdcpp:wan2.2-ti2v-5b"]["configured"] is True
    jid = await _job(env, "sdcpp:wan2.2-ti2v-5b", width=640, height=352, frames=17, steps=16, seed=7)
    assert await process_one(env.svc) == jid
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "completed", job
    run = (await env.client.get("/api/studio/runs")).json()["items"][0]
    out = run["provenance"]["output"]
    assert out["mime"] == "video/mp4" and out["width"] == 640 and out["height"] == 352
    trace = run["provenance"]["settings_resolved"]["engine_trace"]
    assert trace["engine"] == "stable-diffusion.cpp"
    # the mock prints no device line: the backend is UNVERIFIED, never asserted
    assert trace["backend"]["declared"] == "vulkan" and trace["backend"]["status"] == "UNVERIFIED"
    gen = trace["generation"]
    assert set(gen["model_files"]) == {"diffusion", "vae", "text_encoder"}
    for f in gen["model_files"].values():
        assert f["sha256_expected"] == f["sha256_observed"] and len(f["sha256_observed"]) == 64
    assert len(gen["raw_output"]["sha256"]) == 64 and gen["elapsed_s"] is not None
    assert "-M" in gen["argv"] and "vid_gen" in gen["argv"]
    assert gen["duration_s_declared"] == 17 / 16
    assert trace["transcode"]["tool"] == "ffmpeg" and trace["import"]["sha256"] == out["sha256"]
    assert trace["transcode"]["output_sha256"] == out["sha256"] != gen["raw_output"]["sha256"]
    # fake engine never produces a "verified" capability claim
    models = {m["id"]: m for m in (await env.client.get("/api/studio/models")).json()["items"]}
    assert models["sdcpp:wan2.2-ti2v-5b"]["verified"] is False


async def test_engine_failure_is_named_not_success(env, engine):
    engine["value"] = "fail"
    jid = await _job(env, "sdcpp:z-image-turbo", width=256, height=256, steps=4, seed=1)
    await process_one(env.svc)
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "failed" and job["studio"]["reason"] == "malformed"
    assert (await env.client.get("/api/studio/runs")).json()["total"] == 0


async def test_cancel_kills_engine_process(env, engine):
    engine["value"] = "slow"
    jid = await _job(env, "sdcpp:z-image-turbo", width=256, height=256, steps=4, seed=1)
    worker = asyncio.create_task(process_one(env.svc))
    await asyncio.sleep(3)
    assert (await env.client.post(f"/api/studio/jobs/{jid}/cancel")).status_code == 200
    await asyncio.wait_for(worker, 30)
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] in ("canceled", "cancelled", "failed") and job["status"] != "completed"
    assert (await env.client.get("/api/studio/runs")).json()["total"] == 0
    work = Path(env.settings.data_dir) / "studio" / "engine-work"
    assert not list(work.glob("*.png")), "canceled engine output left behind"
    assert not list(work.glob("*.job.json")), "canceled job sidecar left behind"


async def test_manifest_size_mismatch_is_not_configured(env, engine, tmp_path):
    (tmp_path / "media" / "wan2.2-ti2v-5b" / "vae.bin").write_bytes(b"tampered-longer")
    models = {m["id"]: m for m in (await env.client.get("/api/studio/models")).json()["items"]}
    assert models["sdcpp:wan2.2-ti2v-5b"]["configured"] is False
    assert models["sdcpp:z-image-turbo"]["configured"] is True


async def test_spawn_failure_reaches_api_as_named_failure(env, engine, monkeypatch):
    real = sdcpp._argv
    monkeypatch.setattr(sdcpp, "_argv", lambda *a, **k: [str(Path(env.settings.data_dir) / "no-such-sd-cli"),
                                                        *real(*a, **k)[1:]])
    jid = await _job(env, "sdcpp:z-image-turbo", width=256, height=256, steps=4, seed=1)
    await process_one(env.svc)
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "failed" and job["studio"]["reason"] == "provider_down", job


async def test_zero_exit_corrupt_container_is_failed_not_completed(env, engine):
    engine["value"] = "corrupt"
    jid = await _job(env, "sdcpp:wan2.2-ti2v-5b", width=640, height=352, frames=17, steps=16, seed=7)
    await process_one(env.svc)
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "failed" and job["studio"]["reason"] == "malformed", job
    assert (await env.client.get("/api/studio/runs")).json()["total"] == 0


async def test_same_size_corruption_is_not_configured(env, engine, tmp_path):
    target = tmp_path / "media" / "wan2.2-ti2v-5b" / "vae.bin"
    st = target.stat()
    target.write_bytes(b"VAE")  # same length as "vae", different bytes
    import os
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    models = {m["id"]: m for m in (await env.client.get("/api/studio/models")).json()["items"]}
    assert models["sdcpp:wan2.2-ti2v-5b"]["configured"] is False
    assert models["sdcpp:z-image-turbo"]["configured"] is True


# ---------------------------------------------------------------- length presets (1 с TestRun / 5 / 10 / 15 / 30 с)

@pytest.mark.parametrize("length,segments,frames,duration", [
    ("custom", 1, 49, 49 / 16), ("test_1s", 1, 17, 17 / 16), ("5s", 1, 81, 81 / 16),
    ("10s", 2, 81, 161 / 16), ("15s", 3, 81, 241 / 16), ("30s", 6, 81, 481 / 16)])
def test_length_presets_resolve_frames_segments_and_declared_duration(length, segments, frames, duration):
    base = {"length": length, "width": 832, "height": 480, "frames": 49, "fps": 16, "steps": 20,
            "cfg_scale": 5.0, "seed": 7}
    s = sdcpp.apply_length(base)
    assert sdcpp.segments_for(s) == segments and s["frames"] == frames and s["fps"] == 16
    # joins drop the repeated start frame, so N segments of F frames are F + (F-1)(N-1) frames
    assert sdcpp.declared_duration_s(s) == duration
    if length == "test_1s":
        assert (s["width"], s["height"], s["steps"]) == (640, 352, 16), "TestRun is the cheapest preset"
    elif length != "custom":
        assert (s["width"], s["height"], s["steps"]) == (832, 480, 20), "presets keep owner size and steps"


def test_length_values_are_in_the_catalog_and_default_keeps_old_behaviour():
    from bcc.studio.catalog import load
    wan = next(m for m in load()["models"] if m["id"] == "sdcpp:wan2.2-ti2v-5b")
    schema = wan["settings"]["length"]
    assert schema["default"] == "custom"
    assert set(schema["values"]) == {"custom", *sdcpp.DURATION_PRESETS}


async def test_ten_second_chain_is_two_segments_joined_without_the_repeated_frame(env, engine):
    seen = []
    real = sdcpp._argv

    def spy(cfg, model_id, plane, settings, files_, out, init):
        seen.append({"out": Path(out).name, "init": None if init is None else Path(init).name,
                     "seed": settings["seed"], "frames": settings["frames"]})
        return real(cfg, model_id, plane, settings, files_, out, init)

    sdcpp._argv = spy
    try:
        jid = await _job(env, "sdcpp:wan2.2-ti2v-5b", length="10s", width=640, height=352, steps=16, seed=7)
        assert await process_one(env.svc) == jid
    finally:
        sdcpp._argv = real
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "completed", job
    # segment 2 is I2V from segment 1's last frame, with its own seed
    assert [s["frames"] for s in seen] == [81, 81]
    assert seen[0]["init"] is None and seen[1]["init"] == seen[0]["out"].replace(".webm", "-last.png")
    assert seen[1]["seed"] == seen[0]["seed"] + 1
    run = (await env.client.get("/api/studio/runs")).json()["items"][0]
    trace = run["provenance"]["settings_resolved"]["engine_trace"]
    segs = trace["generation"]["segments"]
    assert len(segs) == 2 and segs[1]["image_to_video"] is True and len(segs[1]["start_frame_sha256"]) == 64
    assert trace["transcode"]["operation"] == "concat_segments_drop_repeated_start_frame"
    assert trace["transcode"]["input_sha256"] == [s["sha256"] for s in segs]
    assert trace["generation"]["duration_s_declared"] == 161 / 16
    # the fake engine writes 1 s (16 frames) per segment: 16 + 15 frames = 1.9375 s after the join
    assert abs(trace["generation"]["duration_s_observed"] - 31 / 16) < 0.07
    work = Path(env.settings.data_dir) / "studio" / "engine-work"
    assert not [p.name for p in work.iterdir() if p.suffix in (".webm", ".png", ".json") and p.name.startswith(segs[0]["name"][:16])]


async def test_testrun_preset_with_default_size_completes_and_plane_matches_output(env, engine):
    # Live regression 2026-09-22: the preset was applied only inside the provider, so the stored
    # plane kept 832x480 and persist() rejected the 640x352 TestRun clip ("output.width mismatch").
    jid = await _job(env, "sdcpp:wan2.2-ti2v-5b", length="test_1s", seed=7)
    assert await process_one(env.svc) == jid
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert job["status"] == "completed", job
    plane = job["studio"]["plane"]["settings"]
    assert (plane["width"], plane["height"], plane["frames"], plane["steps"]) == (640, 352, 17, 16)
    run = (await env.client.get("/api/studio/runs")).json()["items"][0]
    assert (run["provenance"]["output"]["width"], run["provenance"]["output"]["height"]) == (640, 352)
