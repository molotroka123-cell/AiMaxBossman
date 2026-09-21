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
