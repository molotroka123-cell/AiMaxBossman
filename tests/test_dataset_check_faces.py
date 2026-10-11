"""tools/dataset_check/check_faces.py — pure metrics, verdict logic, pipeline on synthetic data (no real faces)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "dataset_check"))
import check_faces as cf  # noqa: E402


def synthetic_face(size=400, yaw_px=0, blur=0, seed=0, bg=200) -> np.ndarray:
    """Cartoon face: skin oval, eyes, nose, mouth. Enough for Haar/yolo sometimes; deterministic for tests."""
    rng = np.random.default_rng(seed)
    img = np.full((size, size, 3), bg, np.uint8)
    img = cv2.add(img, rng.integers(0, 12, img.shape, dtype=np.uint8))   # texture so Laplacian var is not 0
    c = size // 2
    cv2.ellipse(img, (c, c), (int(size * 0.28), int(size * 0.38)), 0, 0, 360, (150, 180, 220), -1)
    for dx in (-int(size * 0.11), int(size * 0.11)):
        cv2.circle(img, (c + dx + yaw_px, c - int(size * 0.08)), int(size * 0.035), (255, 255, 255), -1)
        cv2.circle(img, (c + dx + yaw_px, c - int(size * 0.08)), int(size * 0.016), (30, 30, 30), -1)
    cv2.line(img, (c + yaw_px, c - int(size * 0.02)), (c + yaw_px, c + int(size * 0.1)), (90, 110, 160), 3)
    cv2.ellipse(img, (c + yaw_px, c + int(size * 0.2)), (int(size * 0.09), int(size * 0.03)), 0, 0, 180, (60, 60, 160), 3)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    return img


def lm68(yaw=0.0, pitch=0.4, iod=60.0) -> np.ndarray:
    """Synthetic 68-point layout with only the indices head_pose_from_68 uses."""
    lm = np.zeros((68, 2), np.float32)
    lm[36:42] = [100, 100]
    lm[42:48] = [100 + iod, 100]
    eyes = np.array([100 + iod / 2, 100])
    chin_y = 100 + 150
    lm[30] = [eyes[0] + yaw * iod, 100 + pitch * (chin_y - 100)]
    lm[8] = [eyes[0], chin_y]
    lm[0], lm[16] = [60, 110], [140 + iod, 110]
    return lm


# ------------------------------------------------------------------ pure functions
def test_phash_stable_under_small_change_and_differs_for_other_image():
    a = cv2.cvtColor(synthetic_face(seed=1), cv2.COLOR_BGR2GRAY)
    a_small = cv2.resize(a, (300, 300))
    a_jpeg = cv2.imdecode(cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, 80])[1], cv2.IMREAD_GRAYSCALE)
    b = cv2.cvtColor(synthetic_face(seed=2, yaw_px=30, bg=90), cv2.COLOR_BGR2GRAY)
    h = cf.phash(a)
    assert cf.hamming(h, cf.phash(a_small)) <= 6
    assert cf.hamming(h, cf.phash(a_jpeg)) <= 6
    assert cf.hamming(h, cf.phash(b)) > 6
    assert cf.hamming(h, cf.phash(255 - a)) >= 40     # negative control: inverted image is far


def test_sharpness_orders_blur():
    s0 = cf.sharpness(cv2.cvtColor(synthetic_face(), cv2.COLOR_BGR2GRAY))
    s3 = cf.sharpness(cv2.cvtColor(synthetic_face(blur=3), cv2.COLOR_BGR2GRAY))
    s8 = cf.sharpness(cv2.cvtColor(synthetic_face(blur=8), cv2.COLOR_BGR2GRAY))
    assert s0 > s3 > s8


@pytest.mark.parametrize("yaw,pitch", [(0.0, 0.40), (0.3, 0.40), (-0.5, 0.55), (0.0, 0.25)])
def test_head_pose_roundtrip(yaw, pitch):
    y, p = cf.head_pose_from_68(lm68(yaw, pitch))
    assert abs(y - yaw) < 1e-4 and abs(p - pitch) < 1e-4
    assert cf.yaw_bucket(y) == ("frontal" if abs(yaw) <= 0.15 else "right" if yaw > 0 else "left")


def _item(**kw) -> cf.Item:
    base = dict(file="a.jpg", frame=None, faces=1, face_px=300.0, sharpness=200.0, consistency=0.8, yaw=0.05, pitch=0.4)
    base.update(kw)
    return cf.Item(**base)


def test_verdict_accept_and_each_reject_reason():
    t = cf.Thresholds()
    it = _item()
    cf.verdict_for(it, t, identity_measured=True)
    assert it.verdict == "ACCEPT" and it.reasons == []
    cases = {
        "NO_FACE": dict(faces=0, face_px=None, sharpness=None, consistency=None, yaw=None, pitch=None),
        "MULTI_FACE": dict(faces=2),
        "FACE_TOO_SMALL": dict(face_px=120.0),
        "BLURRY": dict(sharpness=10.0),
        "OUTLIER_IDENTITY": dict(consistency=0.3),
        "EXTREME_YAW": dict(yaw=0.9),
        "EXTREME_PITCH": dict(pitch=0.7),
        "DUPLICATE_OF": dict(dup_of="b.jpg"),
    }
    for reason, kw in cases.items():
        it = _item(**kw)
        cf.verdict_for(it, t, identity_measured=True)
        assert it.verdict == "REJECT", reason
        assert any(r.startswith(reason) for r in it.reasons), (reason, it.reasons)


def test_verdict_without_identity_is_insufficient_not_accept():
    it = _item(consistency=cf.NOT_RUN, yaw=cf.NOT_RUN, pitch=cf.NOT_RUN)
    cf.verdict_for(it, cf.Thresholds(), identity_measured=False)
    assert it.verdict == "INSUFFICIENT_EVIDENCE" and it.reasons == ["IDENTITY_NOT_MEASURED"]
    it = _item(consistency=cf.NOT_RUN, yaw=cf.NOT_RUN, pitch=cf.NOT_RUN, sharpness=5.0)   # a real defect still rejects
    cf.verdict_for(it, cf.Thresholds(), identity_measured=False)
    assert it.verdict == "REJECT" and it.reasons[0].startswith("BLURRY")


def test_allow_multi_only_relaxes_multi_face():
    it = _item(faces=3)
    cf.verdict_for(it, cf.Thresholds(allow_multi=True), identity_measured=True)
    assert it.verdict == "ACCEPT"
    it = _item(faces=3, sharpness=1.0)
    cf.verdict_for(it, cf.Thresholds(allow_multi=True), identity_measured=True)
    assert it.verdict == "REJECT"


# ------------------------------------------------------------------ pipeline with an injected backend
class FakeBackend:
    """Detects the synthetic face by its known geometry and returns deterministic embeddings per `seed` colour."""

    name = "onnx"
    versions = {"fake": True}

    def __init__(self, outlier_files=()):
        self.outlier_files = set(outlier_files)
        self.current = None

    def detect(self, frame):
        h, w = frame.shape[:2]
        if frame.mean() > 245:                       # blank image -> no face
            return []
        s = min(h, w)
        c = w // 2, h // 2
        box = np.array([c[0] - s * 0.28, c[1] - s * 0.38, c[0] + s * 0.28, c[1] + s * 0.38], np.float32)
        faces = [cf.Face(bbox=box, score=0.9, kps5=np.zeros((5, 2), np.float32))]
        if frame[5, 5, 0] == 7:                      # marker pixel -> two faces
            faces.append(cf.Face(bbox=box + 5, score=0.8, kps5=np.zeros((5, 2), np.float32)))
        return faces

    def enrich(self, frame, f):
        f.lm68 = lm68(0.0, 0.4)
        e = np.ones(512, np.float32)
        if self.current in self.outlier_files:
            e = -e
        f.embedding = e / np.linalg.norm(e)


def _write(p: Path, img):
    cv2.imencode(".png", img)[1].tofile(str(p))


def test_pipeline_end_to_end(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    _write(src / "good1.png", synthetic_face(seed=1))
    _write(src / "good1_copy.png", synthetic_face(seed=1))              # exact duplicate
    _write(src / "good2.png", synthetic_face(seed=5, yaw_px=20, bg=120))
    _write(src / "blur.png", synthetic_face(seed=3, blur=12))
    _write(src / "small.png", synthetic_face(size=200, seed=4))
    _write(src / "blank.png", np.full((300, 300, 3), 255, np.uint8))
    # distinct files differ in geometry (yaw_px) and background: pairwise phash distance >= 8 (measured)
    two = synthetic_face(seed=6, yaw_px=-25, bg=150)
    two[5, 5] = (7, 7, 7)
    _write(src / "two_faces.png", two)
    _write(src / "other_person.png", synthetic_face(seed=7, yaw_px=35, bg=60))
    (src / "junk.txt").write_text("ignored", encoding="utf-8")
    # a short video: 12 identical frames -> step 10 samples 2 frames; 2nd is a duplicate of the 1st
    vw = cv2.VideoWriter(str(src / "clip.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 10, (400, 400))
    for _ in range(12):
        vw.write(synthetic_face(seed=9, yaw_px=-40, bg=100))
    vw.release()

    backend = FakeBackend(outlier_files={"other_person.png"})
    orig = cf.analyse_frame

    def tracked(b, frame, file, frame_no):
        b.current = file
        return orig(b, frame, file, frame_no)

    monkeypatch.setattr(cf, "analyse_frame", tracked)
    out = tmp_path / "out"
    rep = cf.run_check(src, out, cf.Thresholds(min_face_px=150, min_sharpness=60), backend=backend, video_step=10)

    by = {it["key"]: it for it in rep["items"]}
    assert by["good1.png"]["verdict"] == "ACCEPT"
    assert by["good2.png"]["verdict"] == "ACCEPT"
    assert by["good1_copy.png"]["reasons"] == ["DUPLICATE_OF:good1.png"]
    assert by["blur.png"]["reasons"][0].startswith("BLURRY")
    assert by["small.png"]["reasons"][0].startswith("FACE_TOO_SMALL")
    assert by["blank.png"]["reasons"] == ["NO_FACE"]
    assert by["two_faces.png"]["reasons"] == ["MULTI_FACE:2"]
    assert by["other_person.png"]["reasons"][0].startswith("OUTLIER_IDENTITY")
    # two identical video frames: exactly one survives (the sharper one), the other is its duplicate
    f0, f10 = by["clip.avi#f0"], by["clip.avi#f10"]
    kept, dup = (f0, f10) if f0["verdict"] == "ACCEPT" else (f10, f0)
    assert kept["verdict"] == "ACCEPT" and dup["reasons"] == [f"DUPLICATE_OF:{kept['key']}"]
    assert kept["sharpness"] >= dup["sharpness"]
    assert "junk.txt" not in by and rep["files"] == 9
    s = rep["summary"]
    assert s["accept"] == 3 and s["reject"] == 7 and s["insufficient_evidence"] == 0
    # pHash is blur-invariant: blur.png is the same picture as good1.png. The sharp one must stay the original.
    assert by["blur.png"]["dup_of"] == "good1.png" and by["good1.png"]["dup_of"] is None
    assert s["reject_reasons"]["DUPLICATE_OF"] == 3
    assert (out / "report.json").is_file() and (out / "rejected_contact_sheet.jpg").is_file()
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["backend"] == "onnx"
    csv_lines = (out / "report.csv").read_text(encoding="utf-8").splitlines()
    assert csv_lines[0].startswith("key,file,frame,verdict,reasons") and len(csv_lines) == 1 + s["items"]


def test_opencv_fallback_never_accepts(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _write(src / "a.png", synthetic_face(seed=1))
    rep = cf.run_check(src, tmp_path / "out", cf.Thresholds(), backend=cf.OpenCVFallbackBackend())
    assert rep["backend"] == "opencv" and rep["identity_measured"] is False
    assert rep["summary"]["accept"] == 0
    for it in rep["items"]:
        assert it["verdict"] in ("REJECT", "INSUFFICIENT_EVIDENCE")
        if it["faces"] is None:                       # OpenCV build without a cascade: detector itself NOT_RUN
            assert it["reasons"] == ["DETECTOR_NOT_RUN"] and it["verdict"] == "INSUFFICIENT_EVIDENCE"


# ------------------------------------------------------------------ real ONNX models (FaceFusion assets), CPU
@pytest.mark.skipif(not cf.OnnxBackend.available(cf.DEFAULT_MODELS), reason="FaceFusion ONNX models not installed")
def test_onnx_backend_smoke_on_synthetic(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _write(src / "cartoon.png", synthetic_face(seed=1))
    _write(src / "noise.png", np.random.default_rng(0).integers(0, 255, (320, 320, 3), dtype=np.uint8))
    rep = cf.run_check(src, tmp_path / "out", cf.Thresholds(), models=cf.DEFAULT_MODELS)
    assert rep["backend"] == "onnx" and rep["backend_versions"]["onnxruntime"]
    by = {it["key"]: it for it in rep["items"]}
    assert by["noise.png"]["faces"] == 0 and by["noise.png"]["reasons"] == ["NO_FACE"]
    cartoon = by["cartoon.png"]
    if cartoon["faces"]:                     # a cartoon may or may not be detected; if it is, every metric must be real
        assert isinstance(cartoon["consistency"], float) and isinstance(cartoon["yaw"], float)
        assert cartoon["phash"] and cartoon["sharpness"] > 0


PUBLIC_FACE = Path(r"C:\Users\asd\Bossman\apps-local\facefusion\.github\preview.png")   # FaceFusion's own README preview


@pytest.mark.skipif(not (cf.OnnxBackend.available(cf.DEFAULT_MODELS) and PUBLIC_FACE.is_file()),
                    reason="FaceFusion ONNX models or public preview image not installed")
def test_onnx_backend_real_face_metrics(tmp_path):
    """Real detector + landmarks + ArcFace on a public image (never on the owner's private photos)."""
    src = tmp_path / "in"
    src.mkdir()
    img = cf.read_image(PUBLIC_FACE)
    _write(src / "preview.png", img)
    _write(src / "preview_flip.png", img[:, ::-1])          # mirrored: same person, different phash cluster expected
    rep = cf.run_check(src, tmp_path / "out", cf.Thresholds(min_face_px=80, allow_multi=True), models=cf.DEFAULT_MODELS)
    by = {it["key"]: it for it in rep["items"]}
    a, b = by["preview.png"], by["preview_flip.png"]
    assert a["faces"] >= 1 and a["det_score"] > 0.5 and a["face_px"] > 80
    assert 0.2 <= a["pitch"] <= 0.6 and abs(a["yaw"]) < 0.45          # a frontal preview face
    assert isinstance(a["consistency"], float) and a["consistency"] > 0.5
    assert abs(a["yaw"] + b["yaw"]) < 0.05                               # mirror flips the yaw sign
    assert a["verdict"] == "ACCEPT"
    assert (tmp_path / "out" / "accepted_contact_sheet.jpg").is_file()
