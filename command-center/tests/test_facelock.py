"""FaceLock: the face of the reference goes back into the edited result, exactly.

Pure-function tests on SYNTHETIC face-like images with landmarks injected through a fake detector (no model, no
photo of a real person), negative controls (no face / count mismatch / low confidence / another pose -> skipped,
inputs never modified), the worker CLI, and the job pipeline with a fake worker process.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from bcc.direct_gen import facelock_worker as fl  # noqa: E402
from bcc.direct_gen import sdcli  # noqa: E402

from .test_direct_gen_photo import PNG, FakeSd, create, finish, install  # noqa: E402


def canonical_landmarks() -> np.ndarray:
    """68 points of a frontal face of width 1, centred on (0, 0), y down."""
    pts = []
    for i in range(17):                                              # jaw
        a = np.pi - i * np.pi / 16
        pts.append((0.5 * np.cos(a), 0.05 + 0.55 * np.sin(a)))
    for x in np.linspace(-0.38, -0.10, 5):                           # brows
        pts.append((x, -0.22))
    for x in np.linspace(0.10, 0.38, 5):
        pts.append((x, -0.22))
    for y in np.linspace(-0.12, 0.12, 4):                            # nose bridge
        pts.append((0.0, y))
    for x in np.linspace(-0.12, 0.12, 5):                            # nostrils
        pts.append((x, 0.2))
    for cx in (-0.22, 0.22):                                         # eyes
        for k in range(6):
            a = k * np.pi / 3
            pts.append((cx + 0.07 * np.cos(a), -0.1 + 0.035 * np.sin(a)))
    for rx, ry in ((0.17, 0.06), (0.1, 0.03)):                       # mouth outer / inner
        for k in range(12 if rx > 0.15 else 8):
            a = k * 2 * np.pi / (12 if rx > 0.15 else 8)
            pts.append((rx * np.cos(a), 0.38 + ry * np.sin(a)))
    arr = np.array(pts, np.float64)
    assert arr.shape == (68, 2)
    return arr


def place(width: float, cx: float, cy: float, rot_deg: float = 0.0) -> np.ndarray:
    r = np.radians(rot_deg)
    rot = np.array([[np.cos(r), -np.sin(r)], [np.sin(r), np.cos(r)]])
    return canonical_landmarks() @ rot.T * width + np.array([cx, cy])


def render(size: tuple[int, int], lm: np.ndarray, seed: int, *, blur: float = 0.0, tint=(0, 0, 0)) -> np.ndarray:
    """A face-like picture: noisy background, a skin ellipse with textured features drawn on the landmarks."""
    h, w = size
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(90, 25, (h, w, 3)), 0, 255).astype(np.uint8)
    hull = cv2.convexHull(np.round(lm).astype(np.int32))
    skin = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(skin, hull, 255)
    tex = np.clip(rng.normal(0, 14, (h, w, 3)), -60, 60)
    face = np.clip(np.array([150, 170, 205], np.float32) + tex + np.array(tint, np.float32), 0, 255).astype(np.uint8)
    img[skin > 0] = face[skin > 0]
    for sl in (slice(36, 42), slice(42, 48), slice(48, 60)):
        cv2.fillPoly(img, [np.round(lm[sl]).astype(np.int32)], (40 + seed, 40, 60))
    for sl in (slice(17, 22), slice(22, 27), slice(27, 31)):
        cv2.polylines(img, [np.round(lm[sl]).astype(np.int32)], False, (20, 30, 50), 3)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    return img


class FakeDetector:
    """Looks the picture up by its shape: the tests give the reference and the result different sizes."""

    def __init__(self, table: dict[tuple[int, int], list[fl.FaceInfo]]):
        self.table = table

    def __call__(self, img):
        return list(self.table.get(img.shape[:2], []))


def face(lm: np.ndarray, score: float = 0.9, lm_score: float = 0.9) -> fl.FaceInfo:
    return fl.FaceInfo((float(lm[:, 0].min()), float(lm[:, 1].min()), float(lm[:, 0].max()), float(lm[:, 1].max())), lm, score, lm_score)


REF_SIZE, RES_SIZE = (400, 300), (520, 420)
LM_REF = place(170, 150, 210)
LM_RES = place(200, 215, 270, rot_deg=8)


@pytest.fixture()
def pair():
    ref = render(REF_SIZE, LM_REF, seed=1)
    res = render(RES_SIZE, LM_RES, seed=2, blur=3.0, tint=(12, -8, -10))   # the "model" smeared and tinted the face
    return ref, res


def aligned_diff(ref, img, lm_ref, lm_res, *, demean: bool = True) -> float:
    """Mean abs difference to the reference face after landmark alignment, inside the eroded face hull.
    demean=True compares STRUCTURE (colour matching at the seam is meant to move the average tone)."""
    m = fl.fit_similarity(lm_ref, lm_res)
    warped = cv2.warpAffine(ref, m, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC).astype(np.float32)
    mask = fl.hull_mask(img.shape, fl.apply(m, lm_ref))
    mask = cv2.erode(mask, np.ones((15, 15), np.uint8)) > 0
    a, b = warped[mask], img.astype(np.float32)[mask]
    if demean:
        a, b = a - a.mean(0), b - b.mean(0)
    return float(np.abs(a - b).mean())


def test_applied_face_matches_reference_after_alignment(pair):
    ref, res = pair
    detect = FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(LM_RES)]})
    out, rep = fl.facelock(ref, res, detect)
    assert rep["status"] == "applied" and out is not None and out.shape == res.shape
    before, after = aligned_diff(ref, res, LM_REF, LM_RES), aligned_diff(ref, out, LM_REF, LM_RES)
    assert before > 10, before                           # the unlocked face is clearly different from the reference
    assert after < 0.2 * before, (before, after)         # FaceLock brings its structure back
    # the seam band takes the tone of what the model drew: the pasted face does not glow against its surroundings
    m = fl.fit_similarity(LM_REF, LM_RES)
    inner = cv2.erode(fl.hull_mask(res.shape, fl.apply(m, LM_REF)), np.ones((31, 31), np.uint8)) > 0
    ring = (fl.hull_mask(res.shape, fl.apply(m, LM_REF)) > 0) & ~inner
    lab = lambda x: cv2.cvtColor(x, cv2.COLOR_BGR2LAB).astype(np.float32)[ring].mean(0)
    assert np.abs(lab(out) - lab(res)).max() < 6
    assert rep["scale"] == pytest.approx(200 / 170, rel=0.02) and rep["rotation_deg"] == pytest.approx(8, abs=1.0)


def test_only_the_face_changes_and_the_edge_is_soft(pair):
    ref, res = pair
    detect = FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(LM_RES)]})
    out, _ = fl.facelock(ref, res, detect)
    changed = np.abs(out.astype(np.int16) - res.astype(np.int16)).max(axis=2) > 0
    assert changed.sum() > 4000
    far = np.ones(changed.shape, bool)
    hull = fl.hull_mask(res.shape, fl.hull_points(LM_RES))          # the hull includes the lifted brow ridge
    far[cv2.dilate(hull, np.ones((9, 9), np.uint8)) > 0] = False
    assert not changed[far].any(), "pixels far from the face must be exactly what the model drew"
    ys, xs = np.nonzero(changed)
    assert ys.min() >= 0 and (ys.max() - ys.min()) < 1.6 * 200       # confined to a face-sized region


def test_inputs_are_never_modified(pair):
    ref, res = pair
    ref0, res0 = ref.copy(), res.copy()
    lm_ref0, lm_res0 = LM_REF.copy(), LM_RES.copy()
    detect = FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(LM_RES)]})
    out, _ = fl.facelock(ref, res, detect)
    assert out is not res and out is not ref
    assert np.array_equal(ref, ref0) and np.array_equal(res, res0)
    assert np.array_equal(LM_REF, lm_ref0) and np.array_equal(LM_RES, lm_res0)


def test_skips_with_a_reason_and_never_guesses(pair):
    ref, res = pair
    f_ref, f_res = face(LM_REF), face(LM_RES)
    other = face(place(80, 60, 60))
    cases = [
        ([], [f_res], "no_face_in_reference"),
        ([f_ref], [], "no_face_in_result"),
        ([f_ref], [f_res, other], "face_count_mismatch ref=1 result=2"),
        ([f_ref, other], [f_res], "face_count_mismatch ref=2 result=1"),
        ([f_ref, face(place(80, 250, 60))], [f_res, other], "multiple_faces_unsupported n=2"),
    ]
    for refs, ress, reason in cases:
        out, rep = fl.facelock(ref, res, FakeDetector({REF_SIZE: refs, RES_SIZE: ress}))
        assert out is None and rep["status"] == "skipped" and rep["reason"] == reason, (reason, rep)


def test_low_confidence_skips(pair):
    ref, res = pair
    out, rep = fl.facelock(ref, res, FakeDetector({REF_SIZE: [face(LM_REF, score=0.55)], RES_SIZE: [face(LM_RES)]}))
    assert out is None and rep["reason"].startswith("low_detector_confidence")
    out, rep = fl.facelock(ref, res, FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(LM_RES, lm_score=0.3)]}))
    assert out is None and rep["reason"].startswith("low_landmark_confidence")


def test_another_head_pose_is_refused_not_forced(pair):
    ref, res = pair
    squashed = LM_RES.copy()
    squashed[:, 0] = squashed[:, 0].mean() + (squashed[:, 0] - squashed[:, 0].mean()) * 0.45      # a strongly turned head
    squashed[48:, 0] += np.linspace(-14, 14, 20)                                                  # mouth drifts off-axis
    out, rep = fl.facelock(ref, res, FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(squashed)]}))
    assert out is None and rep["status"] == "skipped" and "pose" in rep["reason"], rep


def test_absurd_rotation_and_scale_are_refused(pair):
    ref, res = pair
    out, rep = fl.facelock(ref, res, FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(place(200, 215, 270, rot_deg=80))]}))
    assert out is None and rep["reason"].startswith("implausible_rotation")
    out, rep = fl.facelock(ref, res, FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(place(1200, 215, 270))]}))
    assert out is None and rep["reason"].startswith("implausible_scale")


def test_a_face_in_a_plain_picture_is_not_invented():
    blank = np.full((300, 300, 3), 128, np.uint8)
    out, rep = fl.facelock(blank, blank.copy(), FakeDetector({}))
    assert out is None and rep["reason"] == "no_face_in_reference"


def test_similarity_fit_recovers_a_known_transform():
    src = canonical_landmarks()
    r = np.radians(-17)
    true = np.array([[1.7 * np.cos(r), -1.7 * np.sin(r), 30], [1.7 * np.sin(r), 1.7 * np.cos(r), -12]])
    got = fl.fit_similarity(src, fl.apply(true, src))
    assert np.allclose(got, true, atol=1e-6)
    mirrored = src * np.array([-1, 1])
    m = fl.fit_similarity(src, mirrored)                 # a reflection must never be returned: det > 0
    assert np.linalg.det(m[:, :2]) > 0


# ------------------------------------------------------------------ worker CLI + job pipeline


def _png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def test_worker_cli_writes_locked_file_and_keeps_the_reference(pair, tmp_path, monkeypatch):
    ref, res = pair
    (tmp_path / "ref.png").write_bytes(_png(ref))
    (tmp_path / "res.png").write_bytes(_png(res))
    before = (tmp_path / "ref.png").read_bytes()
    monkeypatch.setattr(fl, "facefusion_detector", lambda: FakeDetector({REF_SIZE: [face(LM_REF)], RES_SIZE: [face(LM_RES)]}))
    rc = fl.main([str(tmp_path / "ref.png"), str(tmp_path / "res.png"), str(tmp_path / "out.png"), str(tmp_path / "rep.json")])
    assert rc == 0 and (tmp_path / "out.png").is_file()
    assert json.loads((tmp_path / "rep.json").read_text())["status"] == "applied"
    assert (tmp_path / "ref.png").read_bytes() == before
    monkeypatch.setattr(fl, "facefusion_detector", lambda: FakeDetector({}))
    (tmp_path / "out2.png").unlink(missing_ok=True)
    fl.main([str(tmp_path / "ref.png"), str(tmp_path / "res.png"), str(tmp_path / "out2.png"), str(tmp_path / "rep2.json")])
    assert not (tmp_path / "out2.png").exists()
    assert json.loads((tmp_path / "rep2.json").read_text())["reason"] == "no_face_in_reference"


class FakeRun:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, "", stderr


def fake_worker(mode: str):
    """A stand-in for the FaceFusion-interpreter subprocess: writes what facelock_worker would write."""
    def runner(argv, cwd, timeout):
        locked, rep = Path(argv[-2]), Path(argv[-1])
        if mode == "applied":
            locked.write_bytes(PNG + b"LOCKED")
            rep.write_text(json.dumps({"status": "applied", "reason": "", "scale": 1.1}), encoding="utf-8")
        elif mode == "skipped":
            rep.write_text(json.dumps({"status": "skipped", "reason": "face_count_mismatch ref=1 result=2"}), encoding="utf-8")
        elif mode == "crash":
            return FakeRun(1, "Traceback: boom")
        elif mode == "wrong_size":
            locked.write_bytes(PNG[:16] + (64).to_bytes(4, "big") + PNG[20:])
            rep.write_text(json.dumps({"status": "applied"}), encoding="utf-8")
        return FakeRun(0)
    return runner


def with_facefusion(svc, tmp_path):
    home = tmp_path / "facefusion"
    (home / ".venv" / "Scripts").mkdir(parents=True)
    (home / "facefusion.py").write_text("#")
    (home / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ")
    (home / ".venv" / "bin").mkdir(parents=True)
    (home / ".venv" / "bin" / "python").write_bytes(b"x")
    svc.faceswap_home = home


def b64(data=PNG) -> str:
    return base64.b64encode(data).decode()


async def test_job_keeps_both_files_and_records_provenance(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    with_facefusion(svc, tmp_path)
    svc.facelock_runner = fake_worker("applied")
    r = await create(env, model="qwen-image-2.1-uc-edit", prompt="portrait of @1 at the beach", image_b64=b64(), facelock=True)
    assert r.status_code == 202, r.text
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and job["facelock"]["status"] == "applied"
    assert job["provenance"]["facelock"] == "applied"
    folder = svc.store.job_dir("owner", job["job_id"]) / "result"
    assert (folder / "image.png").read_bytes() == PNG + b"LOCKED"                 # result = locked
    assert (folder / "image_unlocked.png").read_bytes() == PNG                    # the model's own picture is kept
    assert job["result"]["bytes"] == len(PNG + b"LOCKED") and job["result_unlocked"]["bytes"] == len(PNG)
    assert not (svc.store.job_dir("owner", job["job_id"]) / "facelock-tmp").exists()
    got = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file", headers={"X-Participant": "owner"})
    assert got.content == PNG + b"LOCKED"
    un = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file?variant=unlocked", headers={"X-Participant": "owner"})
    assert un.status_code == 200 and un.content == PNG
    bad = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file?variant=other", headers={"X-Participant": "owner"})
    assert bad.status_code == 422
    again = await env.client.post(f"/api/direct-gen/jobs/{job['job_id']}/retry", headers={"X-Participant": "owner"})
    redo = await finish(env, again.json()["job_id"])
    assert redo["facelock"]["requested"] is True and redo["facelock"]["status"] == "applied"


@pytest.mark.parametrize("mode,reason", [("skipped", "skipped(face_count_mismatch ref=1 result=2)"),
                                         ("crash", "skipped(worker_error code=1 Traceback: boom)"),
                                         ("wrong_size", "skipped(locked_image_missing_or_wrong_size)")])
async def test_job_skips_honestly_and_keeps_the_original(env, tmp_path, mode, reason):
    svc = install(env, tmp_path, FakeSd)
    with_facefusion(svc, tmp_path)
    svc.facelock_runner = fake_worker(mode)
    r = await create(env, model="qwen-image-2.1-uc-edit", prompt="x @1", image_b64=b64(), facelock=True)
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["facelock"]["status"] == "skipped" and job["provenance"]["facelock"] == reason
    folder = svc.store.job_dir("owner", job["job_id"]) / "result"
    assert (folder / "image.png").read_bytes() == PNG and not (folder / "image_unlocked.png").exists()
    assert "result_unlocked" not in job
    nope = await env.client.get(f"/api/direct-gen/jobs/{job['job_id']}/file?variant=unlocked", headers={"X-Participant": "owner"})
    assert nope.status_code == 404


async def test_without_facefusion_it_is_skipped_not_failed(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    svc.faceswap_home = tmp_path / "nowhere"
    r = await create(env, model="qwen-image-2.1-uc-edit", prompt="x @1", image_b64=b64(), facelock=True)
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed" and job["provenance"]["facelock"].startswith("skipped(detector_unavailable")


async def test_facelock_is_off_by_default_and_refused_without_a_reference(env, tmp_path):
    svc = install(env, tmp_path, FakeSd)
    called = []
    svc.facelock_runner = lambda *a: called.append(a)
    r = await create(env, model="qwen-image-2.1-uc-edit", prompt="x @1", image_b64=b64())
    job = await finish(env, r.json()["job_id"])
    assert "facelock" not in job and "facelock" not in job["provenance"] and not called
    nope = await create(env, model="z-image-turbo", prompt="a lighthouse", facelock=True)
    assert nope.status_code == 422 and nope.json()["error"]["code"] == "facelock_needs_reference"
    nope = await create(env, model="qwen-image-2.1-uc-edit", prompt="a lighthouse", facelock=True)      # edit model, no photo
    assert nope.status_code == 422
    junk = await env.client.post("/api/direct-gen/jobs", headers={"X-Participant": "owner"},
                                 json={"model": "qwen-image-2.1-uc-edit", "prompt": "x", "facelock": "maybe", "image_b64": b64()})
    assert junk.status_code == 422                                              # pydantic: bool only
    extra = await env.client.post("/api/direct-gen/jobs", headers={"X-Participant": "owner"},
                                  json={"model": "z-image-turbo", "prompt": "x", "nonsense": 1})
    assert extra.status_code == 422                                             # extra="forbid" still holds


def test_worker_module_runs_under_the_registry_python_argv():
    assert Path(fl.__file__).name == "facelock_worker.py" and sdcli.BY_ID["qwen-image-2.1-uc-edit"]
