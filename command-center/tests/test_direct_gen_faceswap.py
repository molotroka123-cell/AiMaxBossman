"""Face swap job (FaceFusion): pure helpers, refusals, a full fake run, STOP.

No real FaceFusion, no GPU, no network. The engine is a fake runner with the
same interface as SdCliRunner; post-processing is monkeypatched to copy files.
"""
from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path

import pytest

from bcc.direct_gen import faceswap, sdcli
from bcc.direct_gen.service import DirectGenError, DirectGenService

from .conftest import wait_for

TERMINAL = ("completed", "failed", "cancelled")

PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (512).to_bytes(4, "big") + (512).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
VIDEO = b"fake source video bytes"


def make_faceswap_home(tmp_path: Path) -> Path:
    root = tmp_path / "facefusion"
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    (root / "facefusion.py").write_bytes(b"")
    return root


def install_swap(env, tmp_path: Path, runner, *, faceswap_home: Path | None = None,
                 verify=lambda p: {"playable": True, "verified": True}):
    if hasattr(runner, "instances"):
        runner.instances = []
    svc = DirectGenService(
        env.svc, models_dir=tmp_path / "models", workflows_dir=tmp_path / "workflows",
        poll_seconds=0.01, verify=verify, media_dir=tmp_path / "media",
        sd_bin=tmp_path / "sd-cli.exe", sd_runner=runner,
        free_memory=lambda: 64 * 1024 ** 3, foreign_engines=lambda: [])
    svc.faceswap_home = faceswap_home if faceswap_home is not None else make_faceswap_home(tmp_path)
    env.svc.direct_gen = svc
    return svc


def swap_body(**over):
    body = {"consent": True, "video_b64": base64.b64encode(VIDEO).decode(),
            "faces_b64": [base64.b64encode(PNG).decode()], "preset": "fast", "frame": "16:9"}
    body.update(over)
    return body


async def finish(svc, job_id, timeout=5):
    async def check():
        job = svc.get("owner", job_id)
        return job if job["status"] in TERMINAL else None
    return await wait_for(check, timeout=timeout)


REAL_POSE_GATE_ARGV = faceswap.pose_gate_argv
FAKE_POSE_GATE = (
    "import json, shutil, sys; src, swp, out, rep = sys.argv[1:5]; shutil.copyfile(swp, out); "
    "open(rep, 'w').write(json.dumps({'frames': 10, 'gated_frames': 2, 'gated_range': [3, 4], 'rows': []}))"
)


@pytest.fixture(autouse=True)
def fake_pose_gate(monkeypatch):
    """The real gate runs under FaceFusion's interpreter; here a plain Python copies the swap and reports 2 gated frames."""
    import sys
    monkeypatch.setattr(faceswap, "pose_gate_argv",
                        lambda root, source, swapped, output, report: [sys.executable, "-c", FAKE_POSE_GATE,
                                                                        str(source), str(swapped), str(output), str(report)])


class FakeFaceFusion:
    """Scripted FaceFusion: writes --output-path, reports 5/10 then 10/10."""

    instances: list["FakeFaceFusion"] = []

    def __init__(self, progress=None):
        self.progress = progress
        self.argv: list[str] = []
        self.killed = False
        self.proc = None
        FakeFaceFusion.instances.append(self)

    async def run(self, argv, *, cwd, on_progress=None):
        self.argv = list(argv)
        out = Path(argv[argv.index("--output-path") + 1])
        await on_progress(5, 10)
        await asyncio.sleep(0.01)
        await on_progress(10, 10)
        out.write_bytes(b"swapped")
        return sdcli.RunResult(0, ["ok"], 123 * 1048576, 0.5)

    def kill(self):
        self.killed = True


class FakeHoldFaceFusion:
    """Sleeps until killed; used for STOP."""

    instances: list["FakeHoldFaceFusion"] = []

    def __init__(self, progress=None):
        self.progress = progress
        self.argv: list[str] = []
        self.killed = False
        self.proc = None
        FakeHoldFaceFusion.instances.append(self)

    async def run(self, argv, *, cwd, on_progress=None):
        self.argv = list(argv)
        while not self.killed:
            await asyncio.sleep(0.01)
        return sdcli.RunResult(0, ["held"], 0, 0.0)

    def kill(self):
        self.killed = True


# ---------------------------------------------------------------- pure helpers

def test_build_argv_fast_and_quality_flags(tmp_path):
    root = tmp_path / "ff"
    root.mkdir()
    src, target, output = tmp_path / "s.png", tmp_path / "t.mp4", tmp_path / "o.mp4"

    fast = faceswap.build_argv(root, [src], target, output, "fast")
    joined = " ".join(fast)
    assert fast[fast.index("--processors") + 1] == "face_swapper"
    assert "--face-swapper-model hyperswap_1a_256" in joined
    assert "--face-swapper-pixel-boost 512x512" in joined
    assert "--face-enhancer-model gfpgan_1.4" in joined

    quality = faceswap.build_argv(root, [src], target, output, "quality")
    joined = " ".join(quality)
    assert "--face-swapper-model hyperswap_1a_256" in joined
    assert "--face-swapper-pixel-boost 1024x1024" in joined
    assert "--face-mask-types box occlusion" in joined
    assert "--face-occluder-model xseg_1" in joined
    assert "--execution-thread-count 1" in joined


def test_build_argv_rejects_bad_source_count_and_preset(tmp_path):
    root, target, output = tmp_path / "ff", tmp_path / "t.mp4", tmp_path / "o.mp4"
    with pytest.raises(ValueError):
        faceswap.build_argv(root, [], target, output)
    with pytest.raises(ValueError):
        faceswap.build_argv(root, [tmp_path / f"s{i}.png" for i in range(6)], target, output)
    with pytest.raises(ValueError):
        faceswap.build_argv(root, [tmp_path / "s.png"], target, output, preset="turbo")


def test_build_argv_execution_provider_comes_from_env(tmp_path, monkeypatch):
    root, src, target, output = tmp_path / "ff", tmp_path / "s.png", tmp_path / "t.mp4", tmp_path / "o.mp4"
    monkeypatch.setenv("BOSSMAN_FACEFUSION_PROVIDER", "cuda")
    argv = faceswap.build_argv(root, [src], target, output)
    assert argv[argv.index("--execution-providers") + 1] == "cuda"
    argv = faceswap.build_argv(root, [src], target, output, execution="rocm")
    assert argv[argv.index("--execution-providers") + 1] == "rocm"


def test_progress_regex_parses_tqdm_line():
    line = "processing:  45%|####  | 100/222 [00:16<00:20"
    m = faceswap.PROGRESS.search(line)
    assert m is not None
    assert (int(m.group(1)), int(m.group(2))) == (100, 222)


def test_picture_box_finds_inner_picture_and_none_when_blank():
    np = pytest.importorskip("numpy")
    gray = np.zeros((100, 200), dtype=np.uint8)
    gray[20:80, 40:160] = 255
    assert faceswap.picture_box(gray) == (20, 80, 40, 160)
    assert faceswap.picture_box(np.zeros((100, 200), dtype=np.uint8)) is None


def test_fit_16x9_returns_720p_frame():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    frame[20:80, 40:160] = 200
    out = faceswap.fit_16x9(frame, (20, 80, 40, 160))
    assert out.shape == (720, 1280, 3)


# ---------------------------------------------------------------- refusals

async def test_create_swap_requires_consent(env, tmp_path):
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    with pytest.raises(DirectGenError) as exc:
        await svc.create_swap("owner", {})
    assert exc.value.status == 422 and exc.value.code == "consent_required"


async def test_create_swap_requires_faces(env, tmp_path):
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    with pytest.raises(DirectGenError) as exc:
        await svc.create_swap("owner", swap_body(faces_b64=[]))
    assert exc.value.status == 422 and exc.value.code == "faces_required"


async def test_create_swap_rejects_non_image_face(env, tmp_path):
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    bad = base64.b64encode(b"not an image").decode()
    with pytest.raises(DirectGenError) as exc:
        await svc.create_swap("owner", swap_body(faces_b64=[bad]))
    assert exc.value.status == 422 and exc.value.code == "face_invalid"


async def test_create_swap_rejects_unknown_preset(env, tmp_path):
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    with pytest.raises(DirectGenError) as exc:
        await svc.create_swap("owner", swap_body(preset="turbo"))
    assert exc.value.status == 422 and exc.value.code == "invalid_preset"


async def test_create_swap_refuses_when_facefusion_not_installed(env, tmp_path):
    svc = install_swap(env, tmp_path, FakeFaceFusion, faceswap_home=tmp_path / "empty")
    with pytest.raises(DirectGenError) as exc:
        await svc.create_swap("owner", swap_body())
    assert exc.value.status == 409 and exc.value.code == "model_unavailable"


# ---------------------------------------------------------------- full fake run

async def test_full_fake_run_completes_and_cleans_work(env, tmp_path, monkeypatch):
    svc = install_swap(env, tmp_path, FakeFaceFusion)

    def fake_compose(src, dst, width=1280, height=720):
        shutil.copyfile(src, dst)
        return 1

    def fake_finish(video, original, dst, *, fps=30):
        shutil.copyfile(video, dst)

    monkeypatch.setattr(faceswap, "compose_16x9", fake_compose)
    monkeypatch.setattr(faceswap, "finish", fake_finish)

    created = await svc.create_swap("owner", swap_body())
    job = await finish(svc, created["job_id"])

    assert job["status"] == "completed"
    assert job["result"]["file"] == "video.mp4"
    assert job["result"]["bytes"] > 0
    assert job["result"]["verified"] is True
    assert job["progress"] == {"kind": "frames", "value": 10, "max": 10, "percent": 100.0}
    assert job["provenance"]["consent_confirmed"] is True
    assert job["provenance"]["engine_returncode"] == 0
    assert job["inputs"] == {"video": len(VIDEO), "faces": [len(PNG)]}
    assert [t["stage"] for t in job["timeline"]] == ["queued", "loading", "generating", "postprocessing", "completed"]

    folder = svc.store.job_dir("owner", job["job_id"])
    assert (folder / "input_video.bin").read_bytes() == VIDEO
    assert (folder / "input_face0.png").read_bytes() == PNG
    assert (folder / "result" / "video.mp4").is_file()
    assert not (folder / "work").exists()


# ---------------------------------------------------------------- STOP

async def test_stop_cancels_and_removes_result(env, tmp_path):
    svc = install_swap(env, tmp_path, FakeHoldFaceFusion)
    created = await svc.create_swap("owner", swap_body())
    job_id = created["job_id"]

    async def running():
        return bool(FakeHoldFaceFusion.instances and FakeHoldFaceFusion.instances[-1].argv)

    await wait_for(running)
    job = await svc.cancel("owner", job_id)
    assert job["status"] == "cancelled"
    assert FakeHoldFaceFusion.instances[-1].killed is True
    folder = svc.store.job_dir("owner", job_id)
    assert not (folder / "result").exists()
    assert not (folder / "work").exists()


async def test_default_keeps_the_source_framing_and_never_refits(env, tmp_path, monkeypatch):
    """Owner 10.10: the per-frame 16:9 refit made edits look stretched; without an explicit 16:9 the frame is untouched."""
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    refits = []
    monkeypatch.setattr(faceswap, "compose_16x9", lambda *a, **k: refits.append(a) or 1)
    monkeypatch.setattr(faceswap, "finish", lambda video, original, dst, **k: shutil.copyfile(video, dst))
    body = swap_body()
    body.pop("frame")
    created = await svc.create_swap("owner", body)
    job = await finish(svc, created["job_id"])
    assert job["status"] == "completed" and job["params"]["frame"] == "original" and refits == []


def test_pose_alpha_fades_the_swap_out_on_a_bowed_head():
    """10.10 two-person clip: bowed head 0.54-0.62 (frontal face pasted on the crown), normal 0.32-0.46."""
    from bcc.direct_gen.pose_gate_worker import PITCH_OFF, PITCH_ON, pose_alpha
    assert (PITCH_ON, PITCH_OFF) == (0.50, 0.56)
    assert pose_alpha(None) == 1.0                                   # no face: nothing to gate
    assert pose_alpha(0.32) == pose_alpha(0.46) == pose_alpha(0.50) == 1.0
    assert pose_alpha(0.56) == pose_alpha(0.62) == 0.0
    assert abs(pose_alpha(0.53) - 0.5) < 1e-9


def test_pitch_of_separates_frontal_from_bowed_landmarks():
    import numpy as np
    from bcc.direct_gen.pose_gate_worker import pitch_of
    lm = np.zeros((68, 2))
    lm[36:48, 1] = 100                                               # eyes
    lm[8, 1] = 200                                                   # chin
    lm[30, 1] = 140                                                  # nose tip: 0.40 of eyes->chin = frontal
    assert abs(pitch_of(lm) - 0.40) < 1e-9
    lm[30, 1] = 160                                                  # nose foreshortened toward the chin = bowed
    assert abs(pitch_of(lm) - 0.60) < 1e-9


def test_outside_the_padded_face_box_the_source_is_kept_exactly():
    """Gate 1 T2 in production: FaceFusion's video path shifts every pixel's colour; only the face area may change."""
    import numpy as np
    from bcc.direct_gen.pose_gate_worker import face_regions_mask, keep_source_outside_faces
    src = np.full((200, 300, 3), 40, np.uint8)
    eng = np.full((200, 300, 3), 37, np.uint8)                       # engine output: whole frame darker
    eng[90:110, 140:160] = 200                                       # the swapped face
    box = (140.0, 90.0, 160.0, 110.0)                                # side 20 -> padded 12 -> 128..172 x 78..122
    out = keep_source_outside_faces(src, eng, [box])
    m = face_regions_mask(src.shape[:2], [box])
    assert (out[m == 0] == src[m == 0]).all()                        # pixel for pixel outside
    assert (out[90:110, 140:160] == 200).all()                       # the face is the engine's
    assert m[100, 150] == 1.0 and m[0, 0] == 0.0 and 0 < m[79, 150] < 1   # linear seam at the padded edge
    assert keep_source_outside_faces(src, eng, []) is eng            # no face found: engine frame untouched


def test_hairline_guard_keeps_the_target_next_to_its_hair():
    """10.10 kisliy: the swapper painted the source's fringe onto the target's temple; the band at the hair keeps the target."""
    import numpy as np
    from bcc.direct_gen.pose_gate_worker import hairline_guard
    src = np.full((200, 200, 3), 180, np.uint8)
    eng = src.copy()
    eng[60:70, 40:60] = 20                                           # painted fringe right below the hairline
    eng[140:150, 90:110] = 120                                       # the swapped mouth, far from the hair
    hair = np.zeros((200, 200), bool)
    hair[40:58, 30:170] = True
    out = hairline_guard(src, eng, hair, side=150.0)                 # band = 9 px
    residual = (180 - int(out[62, 50, 0])) / (180 - 20)              # soft band: share of the smudge left 4 px from hair
    assert residual <= 0.2
    assert (out[140:150, 90:110] == 120).all()                       # the face itself stays swapped
    assert hairline_guard(src, eng, np.zeros((200, 200), bool), 150.0) is eng


async def test_pose_gate_runs_after_the_swap_and_is_recorded(env, tmp_path, monkeypatch):
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    monkeypatch.setattr(faceswap, "finish", lambda video, original, dst, **k: shutil.copyfile(video, dst))
    created = await svc.create_swap("owner", swap_body(frame="original"))
    job = await finish(svc, created["job_id"])
    assert job["status"] == "completed"
    assert job["provenance"]["pose_gate"] == {"frames": 10, "gated_frames": 2, "gated_range": [3, 4]}
    folder = svc.store.job_dir("owner", job["job_id"])
    assert (folder / "result" / "video.mp4").read_bytes() == b"swapped"


async def test_pose_gate_failure_fails_the_job_honestly(env, tmp_path, monkeypatch):
    import sys
    svc = install_swap(env, tmp_path, FakeFaceFusion)
    monkeypatch.setattr(faceswap, "pose_gate_argv",
                        lambda *a: [sys.executable, "-c", "import sys; print('no detector'); sys.exit(3)"])
    created = await svc.create_swap("owner", swap_body(frame="original"))
    job = await finish(svc, created["job_id"])
    assert job["status"] == "failed" and job["error"]["code"] == "pose_gate_failed"
    assert "no detector" in job["error"]["message"]


def test_pose_gate_argv_uses_the_facefusion_interpreter(tmp_path):
    root = make_faceswap_home(tmp_path)
    argv = REAL_POSE_GATE_ARGV(root, *[tmp_path / n for n in ("s.mp4", "w.mp4", "o.mp4", "r.json")])
    assert argv[0] == str(root / ".venv" / "Scripts" / "python.exe")
    assert Path(argv[1]).name == "pose_gate_worker.py" and Path(argv[1]).is_file()
    assert argv[2:] == [str(tmp_path / n) for n in ("s.mp4", "w.mp4", "o.mp4", "r.json")]


def test_plan_chunks_splits_long_clips_and_keeps_short_or_unknown_ones_whole():
    assert faceswap.plan_chunks(0, 2) == [(0, -1)]
    assert faceswap.plan_chunks(90, 2) == [(0, 89)]                 # < 2 x 60 frames: one model load is cheaper
    assert faceswap.plan_chunks(444, 2) == [(0, 221), (222, 443)]
    assert faceswap.plan_chunks(898, 3) == [(0, 299), (300, 599), (600, 897)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_vfr_source_is_normalized_and_the_video_length_survives_a_shorter_audio(tmp_path):
    """10.10 regressions: a VFR phone clip drifted after chunking, and -shortest cut the picture to a shorter audio."""
    import subprocess
    vfr, cfr, out = tmp_path / "vfr.mp4", tmp_path / "cfr.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=3", "-vf", "setpts='if(lt(N,60),N/(30*TB),2/TB+(N-60)/(15*TB))'",
                    "-fps_mode", "vfr", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(vfr)], check=True)
    assert faceswap.normalize_cfr(vfr, cfr) is True
    nominal, average = faceswap.stream_rates(cfr)
    assert nominal == average
    assert faceswap.normalize_cfr(cfr, tmp_path / "again.mp4") is False      # already constant: nothing written
    faceswap.finish(cfr, vfr, out)
    assert abs(faceswap.video_seconds(out) - faceswap.video_seconds(cfr)) < 0.1
