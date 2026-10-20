"""Video FaceLock: the face of the job's own start picture into every frame, gated on many frames, honest fallback.

Fakes only: the FaceFusion worker is a fake (no model, no person), the "generated" video is a tiny synthetic clip made
by the local ffmpeg on the CPU (skipped when ffmpeg is missing), sd-cli is the scripted fake of the photo tests.
What is proven: requested -> applied with fps/frames/size/audio kept and the original kept as result_unlocked;
unavailable -> labelled fallback; the gate samples >= 8 frames over the whole clip and blocks a low one; STOP before and
during the stage; cancel kills the worker; worker pure functions on synthetic landmarks.
"""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from bcc.direct_gen import facelock_worker as fl  # noqa: E402
from bcc.direct_gen import video_facelock as vf  # noqa: E402
from bcc.direct_gen.service import DirectGenService  # noqa: E402

from .conftest import wait_for  # noqa: E402
from .test_direct_gen import FakeComfy, make_models  # noqa: E402
from .test_direct_gen_photo import FakeSd, finish, install  # noqa: E402
from .test_facelock import face, place, render, with_facefusion  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not installed")
FRAMES = 17                                  # Wan 1 s at 16 fps
FPS = "16/1"


def make_clip(path: Path, *, audio: bool = True, frames: int = FRAMES) -> None:
    """A tiny synthetic clip (moving gradient + optional sine tone): no person, CPU only, < 1 s to make."""
    container = vf.container_of(path)
    argv = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=64x48:rate=16:duration={frames / 16}"]
    if audio:
        argv += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1.2"]
    argv += ["-frames:v", str(frames)]
    argv += (["-c:v", "libvpx", "-b:v", "200k"] if container == "webm" else ["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio:
        argv += ["-c:a", "libopus" if container == "webm" else "aac", "-shortest"]
    argv += ["-f", container, str(path)]
    subprocess.run(argv, check=True, capture_output=True, timeout=60)


def probe(path: Path) -> dict:
    out = subprocess.run(vf.probe_argv("ffprobe", path), capture_output=True, text=True, timeout=60, check=True)
    return vf.parse_probe(out.stdout)


def audio_md5(path: Path) -> str:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True, timeout=60, check=True)
    return out.stdout.strip()


class FakeWorker:
    """Stands in for `facelock_worker.py --frames/--similarity` under FaceFusion's python. Everything else
    (ffprobe/ffmpeg) runs for real through video_facelock.run_proc."""

    def __init__(self, *, marks: str | None = None, sims=None, hold: bool = False, crash: bool = False):
        self.marks, self.sims, self.hold, self.crash = marks, sims, hold, crash
        self.calls: list[list[str]] = []
        self.cancelled = False
        self.sampled: list[str] = []

    async def __call__(self, argv, cwd, timeout, on_line=None):
        if "--frames" not in argv and "--similarity" not in argv:
            return await vf.run_proc(argv, cwd, timeout, on_line)
        self.calls.append(list(argv))
        try:
            if self.hold:
                while True:
                    await asyncio.sleep(0.01)
            if self.crash:
                return vf.Proc(1, ["Traceback: boom"])
            if "--frames" in argv:
                i = argv.index("--frames")
                frames_dir, out_dir, rep = Path(argv[i + 2]), Path(argv[i + 3]), Path(argv[i + 4])
                names = sorted(p.name for p in frames_dir.glob("*.png"))
                marks = self.marks or "a" * len(names)
                for k, name in enumerate(names, start=1):
                    img = cv2.imread(str(frames_dir / name))
                    if marks[k - 1] == "a":
                        img[8:40, 16:48] = (255, 255, 255)          # the "pasted face": visibly different pixels
                    cv2.imwrite(str(out_dir / name), img)
                    if on_line is not None:
                        await on_line(f"FACELOCK {k}/{len(names)}")
                rep.write_text(json.dumps({"status": "done", "frames": len(names), "applied": marks.count("a"),
                                           "no_face": marks.count("n"), "refused": marks.count("r"),
                                           "refusals": {}, "marks": marks}), encoding="utf-8")
            else:
                i = argv.index("--similarity")
                samples_dir, rep = Path(argv[i + 2]), Path(argv[i + 3])
                files = sorted(p.name for p in samples_dir.glob("*.png"))
                self.sampled = files
                locked = [f for f in files if f.startswith("s")]
                vals = self.sims or [0.9] * len(locked)
                rows = [{"file": f, "similarity": vals[k], "reason": ""} for k, f in enumerate(locked)]
                rows += [{"file": f, "similarity": 0.3, "reason": ""} for f in files if f.startswith("u")]
                rep.write_text(json.dumps({"status": "done", "samples": rows}), encoding="utf-8")
            return vf.Proc(0, [])
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class ClipSd(FakeSd):
    """The scripted sd-cli writing a real (synthetic) clip where the engine would write its .webm."""

    clip: bytes = b""

    async def run(self, argv, *, cwd, on_progress=None, **_):
        res = await super().run(argv, cwd=cwd, on_progress=on_progress)
        out = Path(argv[argv.index("-o") + 1])
        if res.returncode == 0 and out.suffix == ".webm":
            out.write_bytes(self.clip)
        return res


def png_ref() -> str:
    return base64.b64encode(cv2.imencode(".png", np.full((32, 32, 3), 128, np.uint8))[1].tobytes()).decode()


async def create_video(env, **over):
    body = {"model": "wan2.2-ti2v-5b", "prompt": "waves on a shore", "resolution": "480x272", "seed": 5,
            "duration": 1, "mode": "DIRECT", "image_b64": png_ref(), "facelock": True, **over}
    return await env.client.post("/api/direct-gen/jobs", json=body, headers={"X-Participant": "owner"})


def setup(env, tmp_path, worker: FakeWorker, *, audio=True, frames=FRAMES):
    clip = tmp_path / "clip.webm"
    make_clip(clip, audio=audio, frames=frames)
    ClipSd.clip = clip.read_bytes()
    svc = install(env, tmp_path, ClipSd)
    with_facefusion(svc, tmp_path)
    svc.facelock_video_runner = worker
    svc.stop_poll_seconds = 0.01
    return svc, clip


# ------------------------------------------------------------------ requested -> applied


@needs_ffmpeg
async def test_requested_video_facelock_is_applied_keeping_timing_audio_and_the_original(env, tmp_path):
    worker = FakeWorker()
    svc, clip = setup(env, tmp_path, worker)
    r = await create_video(env)
    assert r.status_code == 202, r.text
    assert r.json()["facelock"] == {"requested": True, "status": "pending", "reason": ""}
    job = await finish(env, r.json()["job_id"], timeout=60)
    assert job["status"] == "completed", job
    fl_ = job["facelock"]
    assert fl_["status"] == "applied" and fl_["kind"] == "video" and fl_["applied"] == FRAMES, fl_
    assert fl_["label"].startswith("FaceLock применён")
    assert job["provenance"]["facelock"] == "applied" and job["provenance"]["facelock_gate"]["passed"] is True
    folder = svc.store.job_dir("owner", job["job_id"]) / "result"
    out, unlocked = folder / "video.webm", folder / "video_unlocked.webm"
    assert unlocked.read_bytes() == clip.read_bytes()                       # the model's own video, byte for byte
    assert out.read_bytes() != clip.read_bytes()
    a, b = probe(out), probe(clip)
    assert (a["frames"], a["fps"], a["width"], a["height"]) == (b["frames"], b["fps"], b["width"], b["height"])
    assert a["has_audio"] and audio_md5(out) == audio_md5(clip)            # the original audio stream, copied
    assert job["result"]["sha256"] != job["result_unlocked"]["sha256"]
    assert not list(folder.glob("*.part")) and not (folder.parent / "facelock-tmp").exists()
    stages = [t["stage"] for t in job["timeline"]]
    assert stages.index("postprocessing") < stages.index("facelock") < stages.index("completed")
    un = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file?variant=unlocked",
                              headers={"X-Participant": "owner"})
    assert un.status_code == 200 and un.content == clip.read_bytes()


@needs_ffmpeg
async def test_gate_samples_at_least_eight_frames_spread_over_the_whole_clip(env, tmp_path):
    worker = FakeWorker()
    setup(env, tmp_path, worker)
    job = await finish(env, (await create_video(env)).json()["job_id"], timeout=60)
    gate = job["facelock"]["gate"]
    assert gate["measured"] >= vf.GATE_MIN_SAMPLES and gate["samples"] == len(gate["frames"])
    assert gate["frames"][0] == 0 and gate["frames"][-1] == FRAMES - 1      # first and last frame both checked
    assert gate["min"] == 0.9 and gate["median"] == 0.9 and gate["unlocked_median"] == 0.3
    assert gate["measured_on"] == "assembled file"
    locked = [f for f in worker.sampled if f.startswith("s")]
    assert len(locked) == len(gate["frames"]) and len([f for f in worker.sampled if f.startswith("u")]) == len(locked)


@needs_ffmpeg
async def test_one_bad_sampled_frame_blocks_shipping_and_labels_the_fallback(env, tmp_path):
    sims = [0.9] * 12
    sims[7] = 0.31
    worker = FakeWorker(sims=sims)
    svc, clip = setup(env, tmp_path, worker)
    job = await finish(env, (await create_video(env)).json()["job_id"], timeout=60)
    assert job["status"] == "completed"
    fl_ = job["facelock"]
    assert fl_["status"] == "skipped" and fl_["reason"].startswith("gate_low_similarity min=0.31")
    assert fl_["label"].startswith("FaceLock не применён: проверка сходства лица не пройдена")
    assert fl_["gate"]["min"] == 0.31 and fl_["gate"]["passed"] is False
    folder = svc.store.job_dir("owner", job["job_id"]) / "result"
    assert (folder / "video.webm").read_bytes() == clip.read_bytes() and "result_unlocked" not in job
    assert not list(folder.glob("*_unlocked*")) and not list(folder.glob("*.part"))


@needs_ffmpeg
async def test_too_few_frames_with_the_face_is_never_judged_on_one_frame(env, tmp_path):
    marks = "nnnnnaaaaannnnnnn"                       # the face only in 5 frames: fewer than 8 measurable samples
    worker = FakeWorker(marks=marks)
    setup(env, tmp_path, worker)
    job = await finish(env, (await create_video(env)).json()["job_id"], timeout=60)
    fl_ = job["facelock"]
    assert fl_["status"] == "skipped" and fl_["reason"].startswith("gate_too_few_samples"), fl_
    assert "мало кадров" in fl_["label"]
    assert len(worker.calls) == 1                     # no similarity run: nothing to judge honestly


@needs_ffmpeg
async def test_many_refused_frames_are_not_shipped_as_a_flickering_lock(env, tmp_path):
    worker = FakeWorker(marks="aaaaaaaaaaaarrrrr")
    setup(env, tmp_path, worker)
    job = await finish(env, (await create_video(env)).json()["job_id"], timeout=60)
    assert job["facelock"]["reason"] == "too_many_refused refused=5/17"
    assert job["facelock"]["label"].startswith("FaceLock не применён: слишком много кадров")


# ------------------------------------------------------------------ unavailable -> labelled fallback


@pytest.mark.parametrize("case,reason,ru", [
    ("no_facefusion", "detector_unavailable", "FaceFusion не установлен"),
    ("no_ffmpeg", "ffmpeg_unavailable", "нет ffmpeg"),
    ("crash", "worker_error code=1", "ошибка FaceLock-обработчика"),
])
@needs_ffmpeg
async def test_unavailable_or_broken_facelock_delivers_the_unlocked_video_labelled(env, tmp_path, case, reason, ru):
    worker = FakeWorker(crash=case == "crash")
    svc, clip = setup(env, tmp_path, worker)
    if case == "no_facefusion":
        svc.faceswap_home = tmp_path / "nowhere"
    if case == "no_ffmpeg":
        svc.media_tools = lambda: None
    job = await finish(env, (await create_video(env)).json()["job_id"], timeout=60)
    assert job["status"] == "completed", job
    fl_ = job["facelock"]
    assert fl_["status"] == "skipped" and fl_["reason"].startswith(reason), fl_
    assert fl_["label"].startswith("FaceLock не применён: ") and ru in fl_["label"]
    assert job["provenance"]["facelock"].startswith(f"skipped({reason}")
    folder = svc.store.job_dir("owner", job["job_id"]) / "result"
    assert (folder / "video.webm").read_bytes() == clip.read_bytes() and "result_unlocked" not in job


async def test_video_facelock_needs_the_start_picture_and_is_off_by_default(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    nope = await create_video(env, image_b64=None)
    assert nope.status_code == 422 and nope.json()["error"]["code"] == "facelock_needs_reference"
    called = []
    svc.facelock_video_runner = lambda *a, **k: called.append(a)
    r = await create_video(env, facelock=False)
    job = await finish(env, r.json()["job_id"])
    assert "facelock" not in job and not called


async def test_comfy_video_refuses_facelock_without_a_start_picture(env, tmp_path):
    models, flows = make_models(tmp_path)
    env.svc.direct_gen = DirectGenService(env.svc, client=FakeComfy(), models_dir=models, workflows_dir=flows,
                                          poll_seconds=0.01, verify=lambda p: {"verified": False, "reason": "test"})
    r = await env.client.post("/api/direct-gen/jobs", headers={"X-Participant": "owner"},
                              json={"model": "wan2.1-t2v-1.3b", "prompt": "a kite", "duration": 2,
                                    "resolution": "480x320", "facelock": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "facelock_needs_reference"


# ------------------------------------------------------------------ STOP and cancel


def stop_file(env) -> Path:
    p = Path(env.svc.settings.data_dir) / "computer" / "STOP"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@needs_ffmpeg
async def test_stop_present_means_the_facelock_stage_never_starts(env, tmp_path):
    worker = FakeWorker()
    svc, clip = setup(env, tmp_path, worker)
    r = await create_video(env)
    stop_file(env).write_text("owner")
    try:
        job = await finish(env, r.json()["job_id"], timeout=60)
    finally:
        stop_file(env).unlink(missing_ok=True)
    assert worker.calls == [] and "facelock" not in [t["stage"] for t in job["timeline"]]
    assert job["facelock"]["reason"] == "stop_active" and "STOP" in job["facelock"]["label"]
    assert (svc.store.job_dir("owner", job["job_id"]) / "result" / "video.webm").read_bytes() == clip.read_bytes()


@needs_ffmpeg
async def test_stop_during_facelock_kills_the_worker_and_keeps_the_model_video(env, tmp_path):
    worker = FakeWorker(hold=True)
    svc, clip = setup(env, tmp_path, worker)
    job_id = (await create_video(env)).json()["job_id"]
    await wait_for(lambda: asyncio.sleep(0, worker.calls or None), timeout=30)
    stop_file(env).write_text("owner")
    try:
        job = await finish(env, job_id, timeout=30)
    finally:
        stop_file(env).unlink(missing_ok=True)
    assert worker.cancelled and job["status"] == "completed"
    assert job["facelock"]["reason"] == "stop_active (FaceLock прерван)"
    assert (svc.store.job_dir("owner", job_id) / "result" / "video.webm").read_bytes() == clip.read_bytes()
    assert not (svc.store.job_dir("owner", job_id) / "facelock-tmp").exists()


@needs_ffmpeg
async def test_cancel_during_facelock_kills_the_worker_and_cancels_the_job(env, tmp_path):
    worker = FakeWorker(hold=True)
    svc, _ = setup(env, tmp_path, worker)
    job_id = (await create_video(env)).json()["job_id"]
    await wait_for(lambda: asyncio.sleep(0, worker.calls or None), timeout=30)
    live = (await env.client.get(f"/api/direct-gen/jobs/{job_id}", headers={"X-Participant": "owner"})).json()
    assert live["stage"] == "facelock" and live["status"] == "postprocessing"
    r = await env.client.post(f"/api/direct-gen/jobs/{job_id}/cancel", headers={"X-Participant": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert worker.cancelled
    folder = svc.store.job_dir("owner", job_id)
    assert not (folder / "result").exists() and not (folder / "facelock-tmp").exists()


# ------------------------------------------------------------------ progress


@needs_ffmpeg
async def test_progress_reports_the_facelock_frames(env, tmp_path):
    seen = []
    worker = FakeWorker()
    svc, _ = setup(env, tmp_path, worker)
    real = svc._emit

    async def spy(job):
        if job.get("stage") == "facelock" and isinstance(job.get("progress_detail"), dict):
            seen.append(dict(job["progress_detail"]))
        await real(job)
    svc._emit = spy
    job = await finish(env, (await create_video(env)).json()["job_id"], timeout=60)
    assert job["facelock"]["status"] == "applied"
    assert seen and seen[-1]["stage"] == "facelock" and seen[-1]["frame"] == FRAMES == seen[-1]["frames"]
    assert job.get("progress_detail") is None or job["progress_detail"].get("stage") != "facelock"


# ------------------------------------------------------------------ pure pieces


def test_sample_indices_spread_and_gate_rules():
    idx = vf.sample_indices(list(range(81)))
    assert len(idx) == vf.GATE_TARGET_SAMPLES and idx[0] == 0 and idx[-1] == 80
    assert max(b - a for a, b in zip(idx, idx[1:])) <= 8
    assert vf.sample_indices([3, 4]) == [3, 4]
    assert vf.gate([0.9] * 8)["passed"] is True
    assert vf.gate([0.9] * 7)["reason"].startswith("gate_too_few_samples")
    g = vf.gate([0.9] * 8 + [None, 0.59])
    assert g["passed"] is False and g["min"] == 0.59 and g["measured"] == 9
    assert vf.label("stop_active") == "FaceLock не применён: активен STOP владельца"
    assert vf.label("weird thing") == "FaceLock не применён: weird thing"


def test_assemble_copies_audio_and_keeps_the_exact_rate():
    argv = vf.assemble_argv("ffmpeg", Path("f"), "30000/1001", Path("v.mp4"), Path("v.mp4.part"), True)
    assert argv[argv.index("-framerate") + 1] == "30000/1001" and argv[argv.index("-r") + 1] == "30000/1001"
    assert argv[argv.index("-c:a") + 1] == "copy" and argv[argv.index("-f") + 1] == "mp4"
    webm = vf.assemble_argv("ffmpeg", Path("f"), "16/1", Path("v.webm"), Path("v.webm.part"), False)
    assert "-c:a" not in webm and webm[webm.index("-f") + 1] == "webm"


class MarkDetector:
    """Frames carry their face table index in pixel (0, 0); the reference is told apart by its shape."""

    def __init__(self, ref_faces, by_mark):
        self.ref_faces, self.by_mark = ref_faces, by_mark

    def __call__(self, img):
        if img.shape[:2] == (400, 300):
            return list(self.ref_faces)
        return list(self.by_mark.get(int(img[0, 0, 0]), []))


def test_worker_locks_every_frame_and_keeps_refused_ones_as_drawn():
    lm_ref, lm_res = place(170, 150, 210), place(200, 215, 270, rot_deg=8)
    ref = render((400, 300), lm_ref, seed=1)
    frames = []
    for k in range(4):
        img = render((520, 420), lm_res, seed=2, blur=3.0)
        img[0, 0] = (k, 0, 0)
        frames.append((f"f{k:06d}.png", img))
    table = {0: [face(lm_res)], 1: [], 2: [face(place(200, 215, 270, rot_deg=80))], 3: [face(lm_res)]}
    written = {}
    report = fl.lock_frames(ref, frames, MarkDetector([face(lm_ref)], table), lambda n, im: written.__setitem__(n, im))
    assert report["marks"] == "anra" and report["applied"] == 2 and report["no_face"] == 1 and report["refused"] == 1
    assert set(written) == {n for n, _ in frames}
    assert np.array_equal(written["f000001.png"], frames[1][1]) and np.array_equal(written["f000002.png"], frames[2][1])
    assert not np.array_equal(written["f000000.png"], frames[0][1])
    none = fl.lock_frames(ref, frames, MarkDetector([], table), lambda *a: None)
    assert none["status"] == "skipped" and none["reason"] == "no_face_in_reference"


def test_worker_similarity_uses_the_recognizer_embedding_only():
    lm = place(170, 150, 210)
    e = np.array([1.0, 0.0, 0.0])
    ref_face = fl.FaceInfo((0, 0, 1, 1), lm, 0.9, 0.9, e)
    same = fl.FaceInfo((0, 0, 1, 1), lm, 0.9, 0.9, np.array([0.8, 0.6, 0.0]))
    samples = [("s001.png", np.zeros((10, 10, 3), np.uint8)), ("s002.png", np.ones((10, 10, 3), np.uint8))]
    rep = fl.similarities(np.zeros((400, 300, 3), np.uint8), samples, MarkDetector([ref_face], {0: [same], 1: []}))
    assert rep["samples"][0]["similarity"] == pytest.approx(0.8) and rep["samples"][1]["similarity"] is None
    noemb = fl.similarities(np.zeros((400, 300, 3), np.uint8), samples,
                            lambda img: [fl.FaceInfo((0, 0, 1, 1), lm, 0.9)])
    assert noemb["status"] == "skipped"


def test_worker_frames_cli_writes_every_frame_and_progress(tmp_path, monkeypatch, capsys):
    lm_ref, lm_res = place(170, 150, 210), place(200, 215, 270, rot_deg=8)
    cv2.imwrite(str(tmp_path / "ref.png"), render((400, 300), lm_ref, seed=1))
    (tmp_path / "frames").mkdir()
    for k in range(3):
        img = render((520, 420), lm_res, seed=2)
        img[0, 0] = (0, 0, 0)
        cv2.imwrite(str(tmp_path / "frames" / f"f{k + 1:06d}.png"), img)
    monkeypatch.setattr(fl, "facefusion_detector", lambda: MarkDetector([face(lm_ref)], {0: [face(lm_res)]}))
    rc = fl.main(["--frames", str(tmp_path / "ref.png"), str(tmp_path / "frames"), str(tmp_path / "out"),
                  str(tmp_path / "rep.json")])
    assert rc == 0
    rep = json.loads((tmp_path / "rep.json").read_text())
    assert rep["marks"] == "aaa" and sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "f000001.png", "f000002.png", "f000003.png"]
    assert capsys.readouterr().out.splitlines()[-1] == "FACELOCK 3/3"
