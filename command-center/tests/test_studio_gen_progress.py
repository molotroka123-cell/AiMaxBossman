"""Live generation progress read from sd-cli output (bcc.studio.progress) and exposed by the Studio API.

The parser is checked on a REAL excerpt of an sd-cli run (Z-Image Turbo 1024x1024, 8 steps,
Radeon 8060S / Vulkan, 2026-10-13): ``tests/fixtures/sdcpp/zimage_turbo_1024_8steps.log``. Bars in
that file are carriage-return separated, exactly as the engine wrote them. Multi-segment and
high/low-noise cases are built from the same real bar format (no complete video log was available).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

import pytest

from bcc.studio import progress as gp

FIXTURE = Path(__file__).parent / "fixtures" / "sdcpp" / "zimage_turbo_1024_8steps.log"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def bar(step, total, rate, unit="s/it"):
    filled = "=" * (50 * step // total)
    return f"\r  |{filled}>{' ' * (49 - len(filled))}| {step}/{total} - {rate:.2f}{unit}\x1b[K".encode()


# ------------------------------------------------------------------ parser on the real excerpt

def test_real_excerpt_is_carriage_return_separated_and_steps_are_parsed():
    data = FIXTURE.read_bytes()
    assert b"\r" in data and b"s/it" in data and b"MB/s" in data, "fixture must stay the raw engine bytes"
    lines, tail = gp.split_updates(data + b"\n")
    assert tail == b""
    steps = [gp.parse_step(line) for line in lines]
    real = [s for s in steps if s is not None]
    assert [s[0] for s in real] == list(range(1, 9)) and {s[1] for s in real} == {8}
    assert real[0][2] == pytest.approx(14.06) and real[-1][2] == pytest.approx(7.60)
    # tensor loading bars are byte transfers, never steps
    assert all(gp.parse_step(line) is None for line in lines if "MB/s" in line or "GB/s" in line)
    assert gp.parse_step("|=====>    | 3/20 - 2.50it/s") == (3, 20, pytest.approx(0.4))


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_real_excerpt_any_chunking_gives_monotonic_progress_capped_below_one(seed):
    data = FIXTURE.read_bytes()
    clock = Clock()
    p = gp.SdProgress(steps=8, segments=1, surface="image", clock=clock)
    rnd = random.Random(seed)
    seen, phases, i = [], [], 0
    while i < len(data):
        n = rnd.randint(1, 300)
        p.feed(data[i:i + n])
        i += n
        clock.t += 1
        snap = p.snapshot()
        seen.append(snap["progress"])
        phases.append(snap["progress_detail"]["phase"])
    assert seen == sorted(seen), "progress must never go back"
    assert max(seen) <= gp.ENGINE_CAP < 1.0
    order = [ph for k, ph in enumerate(phases) if k == 0 or phases[k - 1] != ph]
    assert order[order[0] == "load":] == ["encode", "sample", "decode", "save"]
    detail = p.snapshot()["progress_detail"]
    assert (detail["step"], detail["steps"], detail["s_per_it"]) == (8, 8, 7.6)
    # sampling end = load + encode + sample weights; decode without tiles does not creep with the clock
    w = gp.WEIGHTS["image"]
    assert p.fraction() == pytest.approx(gp.ENGINE_CAP * 1.0 - gp.ENGINE_CAP * w["save"], abs=1e-6)
    p.segment_done(0)
    final = p.snapshot()
    assert final["progress"] == gp.ENGINE_CAP and final["progress_detail"]["phase"] == "verify"
    assert final["progress_detail"]["eta_s"] == 0.0


def test_decode_without_output_does_not_move_the_percentage():
    clock = Clock()
    p = gp.SdProgress(steps=8, clock=clock)
    for line in ("generate_image 1024x1024", "get_learned_condition completed, taking 4.20s"):
        p.feed_line(line)
    for k in range(1, 9):
        clock.t += 7.6
        p.feed(bar(k, 8, 7.6))
    p.feed(b"\n[INFO   ] image.cpp:888  - sampling completed, taking 68.49s\n"
           b"[INFO   ] image.cpp:549  - decoding 1 latents\n")
    before = p.snapshot()["progress"]
    clock.t += 30
    assert p.snapshot()["progress"] == before, "no fake creep while the VAE is silent"


# ------------------------------------------------------------------ phases and segments

def test_video_segments_and_high_low_noise_passes_are_monotonic():
    clock = Clock()
    p = gp.SdProgress(steps=20, segments=3, surface="video", clock=clock)
    seen = []
    for seg in range(3):
        p.begin_segment(seg)
        p.feed(b"[INFO   ] generate_video 832x480x81\n")
        p.feed(b"[INFO   ] get_learned_condition completed, taking 3.10s\n")
        p.feed(b"[INFO   ] sample(high noise) 832x480x81\n")
        for k in range(1, 11):                      # high noise pass: 10 of the 20 steps
            clock.t += 30
            p.feed(bar(k, 10, 30.0))
            seen.append(p.snapshot()["progress"])
        p.feed(b"\n[INFO   ] sampling(high noise) completed, taking 300.00s\n")
        for k in range(1, 11):                      # low noise pass: the other 10
            clock.t += 30
            p.feed(bar(k, 10, 30.0))
            seen.append(p.snapshot()["progress"])
            d = p.snapshot()["progress_detail"]
            assert (d["step"], d["steps"]) == (10 + k, 20)
            assert (d["segment"], d["segments"]) == (seg + 1, 3)
        p.feed(b"\n[INFO   ] generating latent video completed, taking 600.00s\n")
        for k in range(1, 5):
            p.feed(f"[INFO   ] Wan VAE temporal tile {k}/4: latent frames [0, 5)\n".encode())
            seen.append(p.snapshot()["progress"])
        p.feed(b"[INFO   ] decode_first_stage completed, taking 60.00s\n")
        p.feed(b"[INFO   ] generate_video completed in 700.00s\n")
        seen.append(p.snapshot()["progress"])
        assert p.snapshot()["progress"] < (seg + 1) / 3 * gp.ENGINE_CAP + 1e-9
        p.segment_done(seg)
        seen.append(p.snapshot()["progress"])
        assert p.snapshot()["progress"] == pytest.approx((seg + 1) / 3 * gp.ENGINE_CAP, abs=1e-4)
    assert seen == sorted(seen)
    assert seen[-1] == gp.ENGINE_CAP


def test_segment_restart_never_lowers_the_reported_progress():
    p = gp.SdProgress(steps=4, segments=2, clock=Clock())
    p.begin_segment(0)
    for k in range(1, 5):
        p.feed(bar(k, 4, 1.0))
    high = p.snapshot()["progress"]
    p.begin_segment(1)                             # e.g. the next pass starts; tracker resets its phase
    assert p.snapshot()["progress"] >= high


# ------------------------------------------------------------------ ETA

def test_eta_from_measured_seconds_per_step_and_phase_weights():
    clock = Clock()
    p = gp.SdProgress(steps=8, segments=2, clock=clock)
    assert p.snapshot()["progress_detail"]["eta_s"] is None, "no rate measured yet: no ETA"
    p.feed_line("get_learned_condition completed, taking 4.20s")
    for k in range(1, 5):
        p.feed(bar(k, 8, 7.6))
    w = gp.WEIGHTS["image"]
    unit = 7.6 * 8 / w["sample"]
    expected = 4 * 7.6 + (w["decode"] + w["save"]) * unit + 1 * unit   # rest of this pass + one more pass
    assert p.snapshot()["progress_detail"]["eta_s"] == pytest.approx(expected, abs=0.1)
    # it/s is converted to seconds per step
    q = gp.SdProgress(steps=10, clock=Clock())
    q.feed(bar(5, 10, 2.0, "it/s"))
    assert q.snapshot()["progress_detail"]["s_per_it"] == 0.5
    assert q.eta_s() == pytest.approx(5 * 0.5 + (w["decode"] + w["save"]) * (0.5 * 10 / w["sample"]), abs=0.1)


# ------------------------------------------------------------------ API field (real queue, fake engine)

FAKE_ENGINE = r'''
import subprocess, sys, time, shutil
out = sys.argv[sys.argv.index("-o") + 1]
w = sys.argv[sys.argv.index("-W") + 1]; h = sys.argv[sys.argv.index("-H") + 1]
data = open(sys.argv[1], "rb").read()
for i, part in enumerate(data.split(b"\r")):
    sys.stdout.buffer.write((b"\r" if i else b"") + part); sys.stdout.flush()
    if b"s/it" in part:
        time.sleep(0.45)
subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}",
                "-frames:v", "1", out], check=True)
'''


@pytest.fixture
def progress_engine(tmp_path, monkeypatch):
    from bcc.studio.providers import sdcpp
    models = tmp_path / "media"
    files = {}
    for role in ("diffusion", "vae", "text_encoder"):
        p = models / "z-image-turbo" / f"{role}.bin"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(role.encode())
        files[role] = {"path": f"z-image-turbo/{role}.bin", "bytes": p.stat().st_size,
                       "sha256": hashlib.sha256(role.encode()).hexdigest()}
    (models / "MANIFEST.json").write_text(json.dumps(
        {"schema_version": 1, "engine": {"release": "MOCK_ENGINE"},
         "engines": {"z-image-turbo": {"files": files}}}), encoding="utf-8")
    script = tmp_path / "fake_engine.py"
    script.write_text(FAKE_ENGINE, encoding="utf-8")
    monkeypatch.setenv(sdcpp.BIN_ENV, sys.executable)
    monkeypatch.setenv(sdcpp.MODELS_ENV, str(models))
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "data"))
    real_argv = sdcpp._argv

    def fake_argv(cfg, model_id, plane, settings, files_, out, init):
        argv = real_argv(cfg, model_id, plane, settings, files_, out, init)
        return [sys.executable, str(script), str(FIXTURE), *argv[1:]]

    monkeypatch.setattr(sdcpp, "_argv", fake_argv)
    monkeypatch.setattr(sdcpp.SdCppProvider, "fake", True, raising=False)
    monkeypatch.setattr(gp, "RECORD_EVERY_S", 0.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
async def test_api_returns_live_progress_and_detail_then_one_only_after_verification(env, progress_engine):
    from bcc.features.images import process_one
    r = await env.client.post("/api/studio/jobs", json={"model": "sdcpp:z-image-turbo", "prompt": "кот",
                                                        "settings": {"width": 256, "height": 256, "steps": 8, "seed": 1}})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert r.json()["progress_detail"] is None, "nothing parsed yet: no detail, never a fake number"
    worker = asyncio.create_task(process_one(env.svc))
    samples = []
    while not worker.done():
        job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
        if job["status"] == "running" and job["progress_detail"]:
            samples.append((job["progress"], job["progress_detail"]))
        await asyncio.sleep(0.2)
    await worker
    assert samples, "progress was never visible while the engine ran"
    values = [v for v, _ in samples]
    assert values == sorted(values) and all(0 < v <= gp.ENGINE_CAP for v in values)
    mid = [d for _, d in samples if d["phase"] == "sample" and d["step"] and d["step"] < 8]
    assert mid, samples
    assert mid[-1]["steps"] == 8 and mid[-1]["s_per_it"] > 0 and mid[-1]["eta_s"] is not None
    assert {"phase", "step", "steps", "segment", "segments", "s_per_it", "eta_s"} <= set(mid[-1])
    done = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    assert done["status"] == "completed" and done["progress"] == 1
    assert done["progress_detail"]["phase"] == "verify"


async def test_writer_throttles_and_never_lowers_or_completes(env):
    from bcc.studio.runtime import create_job
    import sqlalchemy as sa
    from bcc.v2.images_tables import image_jobs
    job = await create_job(env.svc, {"model": "mock:image", "prompt": "x", "settings": {}})
    async with env.svc.db.session() as s:
        await s.execute(sa.update(image_jobs).where(image_jobs.c.id == job["id"]).values(status="running", progress=0.05))
        await s.commit()
    clock = Clock()
    w = gp.JobProgressWriter(env.svc, job, every_s=2.0, clock=clock)

    def snap(v, step):
        return {"progress": v, "progress_detail": {"phase": "sample", "step": step, "known": True}}

    assert await w(snap(0.30, 3)) is True
    assert await w(snap(0.40, 4)) is False, "inside the throttle window"
    clock.t += 2.5
    assert await w(snap(0.20, 2)) is True                     # detail written, value not lowered
    row = (await env.client.get(f"/api/studio/jobs/{job['id']}")).json()
    assert row["progress"] == pytest.approx(0.30) and row["progress_detail"]["step"] == 2
    clock.t += 2.5
    await w(snap(1.0, 8))
    row = (await env.client.get(f"/api/studio/jobs/{job['id']}")).json()
    assert row["progress"] == pytest.approx(gp.ENGINE_CAP), "1.0 is the runtime's, after verification"
