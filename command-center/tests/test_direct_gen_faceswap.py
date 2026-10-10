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
