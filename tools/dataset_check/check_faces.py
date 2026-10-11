"""Dataset check for face-model training data (CPU only, no uploads, no GPU).

Owner instruction (11.10.2026): "как обучение зайдёт, надо проверять новые данные" -> every new batch of
photos / videos goes through this tool BEFORE it is used for DFM / LoRA training or as FaceFusion references.

Input : a folder (or one file) of photos (jpg/png/webp) and videos (mp4/mov/mkv/webm; frames sampled every --video-step).
Output: <out>/report.json, <out>/report.csv, <out>/rejected_contact_sheet.jpg (+ accepted_contact_sheet.jpg).

Per item (photo or sampled frame):
  faces        number of detected faces (one person expected)
  face_px      min side of the largest face box, px
  sharpness    Laplacian variance of the gray face crop resized to 256x256 (same recipe as tools/trend_edit/face_id.py)
  consistency  ArcFace cosine to the median embedding of the whole set (same embedding FaceFusion uses: arcface_w600k_r50)
  yaw / pitch  rough head pose from the 68 landmarks (2dfan4): yaw = nose offset from the eye midpoint / inter-ocular
               distance (signed, + = turned to image right); pitch = (nose_y - eyes_y) / (chin_y - eyes_y)
               (pose-gate recipe: bowed 0.54-0.62, normal 0.32-0.46)
  brightness   mean gray of the face crop (0..255), for light coverage only
  phash        64-bit DCT perceptual hash of the face crop; dup = Hamming distance <= --phash-dist to an earlier item
  verdict      ACCEPT / REJECT (with reasons) / INSUFFICIENT_EVIDENCE (a required metric is NOT_RUN: no ONNX backend)

Backends: "onnx" = onnxruntime CPU with FaceFusion's own models (yoloface_8n, arcface_w600k_r50, 2dfan4) found in
--models (default C:\\Users\\asd\\Bossman\\apps-local\\facefusion\\.assets\\models or $FACEFUSION_MODELS).
If they are missing: "opencv" = Haar cascade detection only; consistency / yaw / pitch are reported as NOT_RUN and the
verdict can never be ACCEPT (honest: identity was not measured).

Nothing is uploaded; nothing is written except the report folder. Thresholds are CLI flags and are recorded in the report.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

DEFAULT_MODELS = Path(os.environ.get("FACEFUSION_MODELS", r"C:\Users\asd\Bossman\apps-local\facefusion\.assets\models"))
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
NOT_RUN = "NOT_RUN"

# FaceFusion warp template 'arcface_112_v2' (facefusion/face_helper.py)
ARCFACE_112_V2 = np.array([[0.34191607, 0.46157411], [0.65653393, 0.45983393], [0.50022500, 0.64050536],
                           [0.37097589, 0.82469196], [0.63151696, 0.82325089]], np.float32) * 112


@dataclass
class Face:
    bbox: np.ndarray                      # x1, y1, x2, y2 in frame pixels
    score: float
    kps5: np.ndarray | None = None        # 5 points (eyes, nose, mouth corners)
    lm68: np.ndarray | None = None
    embedding: np.ndarray | None = None   # L2-normalised 512-d

    @property
    def size(self) -> float:
        return float(min(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1]))


# --------------------------------------------------------------------------------------- backends
class OnnxBackend:
    """yoloface_8n -> NMS 0.4 -> 2dfan4 (68 landmarks) + arcface_w600k_r50, all on CPUExecutionProvider."""

    name = "onnx"
    FILES = ("yoloface_8n.onnx", "arcface_w600k_r50.onnx", "2dfan4.onnx")

    @classmethod
    def available(cls, models: Path) -> bool:
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return all((models / f).is_file() for f in cls.FILES)

    def __init__(self, models: Path, det_score: float = 0.5):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        prov = ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(str(models / "yoloface_8n.onnx"), so, providers=prov)
        self.rec = ort.InferenceSession(str(models / "arcface_w600k_r50.onnx"), so, providers=prov)
        self.lmk = ort.InferenceSession(str(models / "2dfan4.onnx"), so, providers=prov)
        self.det_score = det_score
        self.versions = {"onnxruntime": ort.__version__, "models": list(self.FILES), "models_dir": str(models)}

    def detect(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        scale = min(640 / w, 640 / h, 1.0)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
        rw, rh = w / small.shape[1], h / small.shape[0]
        canvas = np.zeros((640, 640, 3), np.float32)
        canvas[:small.shape[0], :small.shape[1]] = small
        inp = np.expand_dims(canvas.transpose(2, 0, 1), 0) / 255.0
        out = self.det.run(None, {self.det.get_inputs()[0].name: inp.astype(np.float32)})[0]
        det = np.squeeze(out).T
        boxes_raw, scores_raw, kps_raw = np.split(det, [4, 5], axis=1)
        keep = np.where(scores_raw.ravel() > self.det_score)[0]
        if keep.size == 0:
            return []
        boxes, scores, kps = [], [], []
        for b, s, k in zip(boxes_raw[keep], scores_raw[keep].ravel(), kps_raw[keep]):
            boxes.append([(b[0] - b[2] / 2) * rw, (b[1] - b[3] / 2) * rh, (b[0] + b[2] / 2) * rw, (b[1] + b[3] / 2) * rh])
            scores.append(float(s))
            k = k.reshape(-1, 3)[:, :2].copy()
            k[:, 0] *= rw
            k[:, 1] *= rh
            kps.append(k)
        xywh = [[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes]
        idx = cv2.dnn.NMSBoxes(xywh, scores, self.det_score, 0.4)
        idx = np.array(idx).ravel().tolist() if len(idx) else []
        faces = []
        for i in idx:
            f = Face(bbox=np.array(boxes[i], np.float32), score=scores[i], kps5=kps[i].astype(np.float32))
            faces.append(f)
        return faces

    def enrich(self, frame: np.ndarray, f: Face) -> None:
        f.lm68 = self._landmarks(frame, f.bbox)
        f.embedding = self._embed(frame, f.kps5)

    def _landmarks(self, frame: np.ndarray, bbox: np.ndarray) -> np.ndarray:
        scale = 195 / max(float(np.subtract(bbox[2:], bbox[:2]).max()), 1.0)
        trans = (256 - np.add(bbox[2:], bbox[:2]) * scale) * 0.5
        m = np.array([[scale, 0, trans[0]], [0, scale, trans[1]]], np.float32)
        crop = cv2.warpAffine(frame, m, (256, 256), borderMode=cv2.BORDER_REPLICATE)
        inp = crop.transpose(2, 0, 1).astype(np.float32) / 255.0
        lm, _heat = self.lmk.run(None, {self.lmk.get_inputs()[0].name: [inp]})
        pts = lm[:, :, :2][0] / 64 * 256
        inv = cv2.invertAffineTransform(m)
        return cv2.transform(pts.reshape(1, -1, 2).astype(np.float32), inv).reshape(-1, 2)

    def _embed(self, frame: np.ndarray, kps5: np.ndarray) -> np.ndarray:
        m = cv2.estimateAffinePartial2D(kps5, ARCFACE_112_V2, method=cv2.RANSAC, ransacReprojThreshold=100)[0]
        crop = cv2.warpAffine(frame, m, (112, 112), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_AREA)
        inp = (crop / 127.5 - 1)[:, :, ::-1].transpose(2, 0, 1).astype(np.float32)
        e = self.rec.run(None, {self.rec.get_inputs()[0].name: [inp]})[0].ravel()
        return (e / (np.linalg.norm(e) + 1e-9)).astype(np.float32)


class OpenCVFallbackBackend:
    """Haar cascade only. No identity, no landmarks: those metrics are NOT_RUN (never silently zero)."""

    name = "opencv"

    def __init__(self):
        self.cascade = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):      # removed in OpenCV 5.x wheels
            self.cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self.versions = {"opencv": cv2.__version__, "cascade": self.cascade is not None,
                         "note": "ONNX models not found; identity/pose NOT_RUN" + ("" if self.cascade else "; no cascade: detector NOT_RUN")}

    def detect(self, frame: np.ndarray) -> list[Face] | None:
        if self.cascade is None:
            return None                                                      # detector NOT_RUN, not "no face"
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        rects = self.cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
        return [Face(bbox=np.array([x, y, x + w, y + h], np.float32), score=1.0) for (x, y, w, h) in rects]

    def enrich(self, frame: np.ndarray, f: Face) -> None:
        return None


# --------------------------------------------------------------------------------------- pure metrics
def phash(gray_crop: np.ndarray) -> int:
    """64-bit DCT perceptual hash (pHash recipe: 32x32 gray -> DCT -> top-left 8x8 -> median threshold)."""
    g = cv2.resize(gray_crop, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(g)[:8, :8]
    med = np.median(d.ravel()[1:])      # drop DC term
    bits = (d > med).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def sharpness(gray_crop: np.ndarray) -> float:
    if gray_crop.size == 0:
        return 0.0
    return float(cv2.Laplacian(cv2.resize(gray_crop, (256, 256)), cv2.CV_64F).var())


def head_pose_from_68(lm: np.ndarray) -> tuple[float, float]:
    """(yaw, pitch) from 68-point layout: eyes 36-47, nose tip 30, chin 8, jaw 0/16."""
    le, re_ = lm[36:42].mean(axis=0), lm[42:48].mean(axis=0)
    eyes = (le + re_) / 2
    iod = float(np.linalg.norm(re_ - le)) + 1e-6
    nose, chin = lm[30], lm[8]
    yaw = float((nose[0] - eyes[0]) / iod)
    pitch = float((nose[1] - eyes[1]) / (abs(chin[1] - eyes[1]) + 1e-6))
    return yaw, pitch


def yaw_bucket(yaw: float | None, max_frontal: float = 0.15) -> str:
    if yaw is None:
        return NOT_RUN
    if abs(yaw) <= max_frontal:
        return "frontal"
    return "right" if yaw > 0 else "left"


@dataclass
class Thresholds:
    min_face_px: int = 160
    min_sharpness: float = 60.0
    min_consistency: float = 0.50
    max_abs_yaw: float = 0.45
    pitch_range: tuple[float, float] = (0.20, 0.60)
    phash_dist: int = 6
    allow_multi: bool = False
    det_score: float = 0.5


@dataclass
class Item:
    file: str
    frame: int | None
    faces: int | None = 0            # None = detector NOT_RUN
    face_px: float | None = None
    det_score: float | None = None
    sharpness: float | None = None
    consistency: float | str | None = None
    yaw: float | str | None = None
    pitch: float | str | None = None
    brightness: float | None = None
    phash: str | None = None
    dup_of: str | None = None
    verdict: str = "REJECT"
    reasons: list[str] = field(default_factory=list)
    _emb: np.ndarray | None = field(default=None, repr=False)
    _hash: int | None = field(default=None, repr=False)
    _crop: np.ndarray | None = field(default=None, repr=False)

    @property
    def key(self) -> str:
        return self.file if self.frame is None else f"{self.file}#f{self.frame}"

    def public(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        d["key"] = self.key
        return d


def verdict_for(it: Item, t: Thresholds, identity_measured: bool) -> None:
    """Fills it.verdict / it.reasons. REJECT beats everything; NOT_RUN on a required metric -> INSUFFICIENT_EVIDENCE."""
    r: list[str] = []
    if it.faces is None:
        it.verdict, it.reasons = "INSUFFICIENT_EVIDENCE", ["DETECTOR_NOT_RUN"]
        return
    if it.faces == 0:
        r.append("NO_FACE")
    elif it.faces > 1 and not t.allow_multi:
        r.append(f"MULTI_FACE:{it.faces}")
    if it.face_px is not None and it.face_px < t.min_face_px:
        r.append(f"FACE_TOO_SMALL:{it.face_px:.0f}px<{t.min_face_px}")
    if it.sharpness is not None and it.sharpness < t.min_sharpness:
        r.append(f"BLURRY:{it.sharpness:.0f}<{t.min_sharpness:.0f}")
    if isinstance(it.consistency, float) and it.consistency < t.min_consistency:
        r.append(f"OUTLIER_IDENTITY:{it.consistency:.3f}<{t.min_consistency}")
    if isinstance(it.yaw, float) and abs(it.yaw) > t.max_abs_yaw:
        r.append(f"EXTREME_YAW:{it.yaw:+.2f}")
    if isinstance(it.pitch, float) and not (t.pitch_range[0] <= it.pitch <= t.pitch_range[1]):
        r.append(f"EXTREME_PITCH:{it.pitch:.2f}")
    if it.dup_of:
        r.append(f"DUPLICATE_OF:{it.dup_of}")
    it.reasons = r
    if r:
        it.verdict = "REJECT"
    elif not identity_measured or it.consistency == NOT_RUN or it.yaw == NOT_RUN:
        it.verdict = "INSUFFICIENT_EVIDENCE"
        it.reasons = ["IDENTITY_NOT_MEASURED"]
    else:
        it.verdict = "ACCEPT"


# --------------------------------------------------------------------------------------- pipeline
def iter_inputs(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT:
            yield p


def read_image(p: Path) -> np.ndarray | None:
    data = np.fromfile(str(p), np.uint8)       # cv2.imread fails on non-ASCII Windows paths
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def iter_frames(p: Path, step: int, max_frames: int) -> Iterable[tuple[int, np.ndarray]]:
    cap = cv2.VideoCapture(str(p))
    i, taken = 0, 0
    while taken < max_frames:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            yield i, fr
            taken += 1
        i += 1
    cap.release()


def analyse_frame(backend, frame: np.ndarray, file: str, frame_no: int | None) -> Item:
    it = Item(file=file, frame=frame_no)
    faces = backend.detect(frame)
    if faces is None:
        it.faces = None
        it.consistency = it.yaw = it.pitch = NOT_RUN
        return it
    it.faces = len(faces)
    if not faces:
        return it
    f = max(faces, key=lambda x: x.size)
    backend.enrich(frame, f)
    x1, y1, x2, y2 = [int(round(v)) for v in f.bbox]
    h, w = frame.shape[:2]
    crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    it.face_px = round(f.size, 1)
    it.det_score = round(f.score, 3)
    if crop.size:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        it.sharpness = round(sharpness(gray), 1)
        it.brightness = round(float(gray.mean()), 1)
        it._hash = phash(gray)
        it.phash = f"{it._hash:016x}"
        it._crop = cv2.resize(crop, (160, 160))
    if f.lm68 is not None:
        yaw, pitch = head_pose_from_68(f.lm68)
        it.yaw, it.pitch = round(yaw, 3), round(pitch, 3)
    else:
        it.yaw = it.pitch = NOT_RUN
    it._emb = f.embedding
    it.consistency = NOT_RUN if f.embedding is None else None     # filled after the median is known
    return it


def finalize(items: list[Item], t: Thresholds, identity_measured: bool) -> dict:
    embs = [it._emb for it in items if it._emb is not None]
    if embs:
        center = np.median(np.stack(embs), axis=0)
        center /= np.linalg.norm(center) + 1e-9
        for it in items:
            if it._emb is not None:
                it.consistency = round(float(np.dot(it._emb, center)), 4)
    # pass 1: verdicts without duplicates; pass 2: dedupe, keeping the SHARPEST otherwise-clean item of each
    # cluster (a blurry or rejected copy must never claim the "original" slot and knock out the good frame)
    for it in items:
        verdict_for(it, t, identity_measured)
    clean = sorted((it for it in items if it._hash is not None and it.verdict != "REJECT"),
                   key=lambda it: -(it.sharpness or 0.0))
    kept: list[Item] = []
    for it in clean:
        for q in kept:
            if hamming(it._hash, q._hash) <= t.phash_dist:
                it.dup_of = q.key
                break
        else:
            kept.append(it)
    for it in items:                       # informational for already-rejected items
        if it._hash is not None and it.verdict == "REJECT" and it.dup_of is None:
            for q in kept:
                if hamming(it._hash, q._hash) <= t.phash_dist:
                    it.dup_of = q.key
                    break
    for it in items:
        verdict_for(it, t, identity_measured)
    acc = [it for it in items if it.verdict == "ACCEPT"]
    cov = {"yaw": {"frontal": 0, "left": 0, "right": 0, NOT_RUN: 0}, "brightness": {"dark<80": 0, "mid": 0, "bright>170": 0}}
    for it in acc:
        cov["yaw"][yaw_bucket(it.yaw if isinstance(it.yaw, float) else None)] += 1
        b = it.brightness or 0
        cov["brightness"]["dark<80" if b < 80 else "bright>170" if b > 170 else "mid"] += 1
    reasons: dict[str, int] = {}
    for it in items:
        for r in it.reasons:
            reasons[r.split(":")[0]] = reasons.get(r.split(":")[0], 0) + 1
    return {"items": len(items), "accept": len(acc), "reject": sum(it.verdict == "REJECT" for it in items),
            "insufficient_evidence": sum(it.verdict == "INSUFFICIENT_EVIDENCE" for it in items),
            "reject_reasons": reasons, "coverage_of_accepted": cov,
            "consistency_mean_accepted": round(float(np.mean([it.consistency for it in acc if isinstance(it.consistency, float)])), 4) if acc and embs else NOT_RUN}


def contact_sheet(items: list[Item], path: Path, cols: int = 6, cell: int = 160) -> int:
    items = [it for it in items if it._crop is not None]
    if not items:
        return 0
    rows = math.ceil(len(items) / cols)
    sheet = np.full((rows * (cell + 28), cols * cell, 3), 24, np.uint8)
    for n, it in enumerate(items):
        r, c = divmod(n, cols)
        y, x = r * (cell + 28), c * cell
        sheet[y:y + cell, x:x + cell] = it._crop
        label = (it.reasons[0] if it.reasons else it.verdict)[:26]
        cv2.putText(sheet, label, (x + 2, y + cell + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (80, 200, 255), 1, cv2.LINE_AA)
        cv2.putText(sheet, Path(it.key).name[-26:], (x + 2, y + cell + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(str(path))
    return len(items)


def run_check(inp: Path, out: Path, t: Thresholds, backend=None, models: Path = DEFAULT_MODELS,
              video_step: int = 10, max_frames: int = 200) -> dict:
    t0 = time.time()
    if backend is None:
        backend = OnnxBackend(models, t.det_score) if OnnxBackend.available(models) else OpenCVFallbackBackend()
    identity_measured = backend.name == "onnx"
    items: list[Item] = []
    files = 0
    for p in iter_inputs(inp):
        files += 1
        rel = str(p.relative_to(inp)) if inp.is_dir() else p.name
        if p.suffix.lower() in IMAGE_EXT:
            img = read_image(p)
            if img is None:
                it = Item(file=rel, frame=None)
                it.reasons, it.verdict = ["UNREADABLE"], "REJECT"
                items.append(it)
                continue
            items.append(analyse_frame(backend, img, rel, None))
        else:
            for i, fr in iter_frames(p, video_step, max_frames):
                items.append(analyse_frame(backend, fr, rel, i))
    summary = finalize(items, t, identity_measured)
    out.mkdir(parents=True, exist_ok=True)
    rejected = [it for it in items if it.verdict != "ACCEPT"]
    accepted = [it for it in items if it.verdict == "ACCEPT"]
    summary["rejected_sheet_faces"] = contact_sheet(rejected, out / "rejected_contact_sheet.jpg")
    summary["accepted_sheet_faces"] = contact_sheet(accepted, out / "accepted_contact_sheet.jpg")
    report = {"tool": "tools/dataset_check/check_faces.py", "version": "1.0", "input": str(inp), "files": files,
              "backend": backend.name, "backend_versions": backend.versions, "identity_measured": identity_measured,
              "thresholds": {**t.__dict__, "video_step": video_step, "max_frames_per_video": max_frames},
              "summary": summary, "elapsed_s": round(time.time() - t0, 1), "items": [it.public() for it in items]}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["key", "file", "frame", "verdict", "reasons", "faces", "face_px", "det_score", "sharpness", "consistency",
            "yaw", "pitch", "brightness", "phash", "dup_of"]
    with (out / "report.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for it in items:
            d = it.public()
            d["reasons"] = ";".join(it.reasons)
            w.writerow({k: d.get(k) for k in cols})
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help="folder (recursive) or one photo/video")
    ap.add_argument("--out", required=True, help="report folder")
    ap.add_argument("--models", default=str(DEFAULT_MODELS))
    ap.add_argument("--video-step", type=int, default=10)
    ap.add_argument("--max-frames", type=int, default=200, help="sampled frames per video")
    ap.add_argument("--min-face", type=int, default=160)
    ap.add_argument("--min-sharp", type=float, default=60.0)
    ap.add_argument("--min-consist", type=float, default=0.50)
    ap.add_argument("--max-yaw", type=float, default=0.45)
    ap.add_argument("--pitch", default="0.20,0.60")
    ap.add_argument("--phash-dist", type=int, default=6)
    ap.add_argument("--allow-multi", action="store_true")
    ap.add_argument("--det-score", type=float, default=0.5)
    a = ap.parse_args(argv)
    lo, hi = (float(v) for v in a.pitch.split(","))
    t = Thresholds(a.min_face, a.min_sharp, a.min_consist, a.max_yaw, (lo, hi), a.phash_dist, a.allow_multi, a.det_score)
    rep = run_check(Path(a.input), Path(a.out), t, models=Path(a.models), video_step=a.video_step, max_frames=a.max_frames)
    s = rep["summary"]
    print(f"backend={rep['backend']} files={rep['files']} items={s['items']} ACCEPT={s['accept']} REJECT={s['reject']} "
          f"INSUFFICIENT={s['insufficient_evidence']} reasons={s['reject_reasons']} consistency_mean={s['consistency_mean_accepted']} "
          f"coverage={s['coverage_of_accepted']} -> {Path(a.out) / 'report.json'}")
    return 0 if s["accept"] > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
