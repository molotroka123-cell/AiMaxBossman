"""Gate 1 — video-to-video face swap: prove the animation is preserved, pixel for pixel outside the face.

Run with FaceFusion's interpreter, cwd = the FaceFusion checkout:
    .venv/Scripts/python.exe gate1_faceswap.py --source src.mp4 --result out.mp4 --refs a.jpg,b.jpg --out report.json

Thresholds are fixed HERE, before any run (owner 10.10: «пороги до теста, не постфактум»):
  T1 timing:     same frame count, same fps (±0.01)
  T2 pixels:     outside the dilated face boxes of BOTH clips, every frame's max |source - result| == 0
                 (requires a lossless result: FaceFusion --output-video-encoder libx264rgb --output-video-quality 100)
  T3 motion:     mean 68-landmark displacement source->result <= 0.05 of the inter-ocular distance
  T4 identity:   mean arcface cosine result->refs >= 0.75, per-frame std <= 0.06; source->refs mean <= 0.30
Every frame is measured (no sampling); per-frame rows go to the report.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.cwd()))
from facefusion import state_manager  # noqa: E402

for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
             "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
             "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
             "face_landmarker_score": 0.5, "log_level": "error"}.items():
    state_manager.init_item(k, v)
from facefusion.face_creator import get_many_faces  # noqa: E402

THRESHOLDS = {"fps_tol": 0.01, "pixels_outside_face_max": 0, "landmark_nme_max": 0.05,
              "identity_mean_min": 0.75, "identity_std_max": 0.06, "source_identity_max": 0.30}


def main_face(frame):
    fs = get_many_faces([frame])
    return max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0]) if fs else None


def emb(face):
    e = getattr(face, "embedding_norm", None)
    return e if e is not None else face.embedding / np.linalg.norm(face.embedding)


def frames(path):
    """A folder of PNG frames (the pixel-exact path) or a video decoded with ffmpeg to bgr24 for BOTH clips (one decoder):
    OpenCV mis-decodes yuvj444p (black read as 0,135,0)."""
    if Path(path).is_dir():
        files = sorted(Path(path).glob("*.png"))
        return [cv2.imread(str(f)) for f in files], float("nan")
    import shutil
    import subprocess
    probe = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    w, h, rate = probe.stdout.strip().split(",")[:3]
    w, h = int(w), int(h)
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1)
    raw = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    arr = np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)
    return list(arr), fps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ref = np.mean([emb(main_face(cv2.imread(p))) for p in a.refs.split(",")], axis=0)
    ref /= np.linalg.norm(ref)
    src, sfps = frames(a.source)
    res, rfps = frames(a.result)
    rows, nme, ids, sids, pix_bad = [], [], [], [], 0
    for i, (s, r) in enumerate(zip(src, res)):
        fs_, fr_ = main_face(s), main_face(r)
        mask = np.ones(s.shape[:2], bool)
        for f in (fs_, fr_):
            if f is not None:
                x1, y1, x2, y2 = [int(v) for v in f.bounding_box]
                pad = int(0.6 * max(x2 - x1, y2 - y1))           # the swap/enhance paste area + blur margin
                mask[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad] = False
        outside = int(np.abs(s.astype(np.int16) - r.astype(np.int16))[mask].max()) if mask.any() else 0
        pix_bad += outside > THRESHOLDS["pixels_outside_face_max"]
        row = {"frame": i, "outside_face_max_abs_diff": outside}
        if fs_ is not None and fr_ is not None:
            ls, lr = fs_.landmark_set["68"], fr_.landmark_set["68"]
            iod = float(np.linalg.norm(ls[36:42].mean(0) - ls[42:48].mean(0))) or 1.0
            row["landmark_nme"] = round(float(np.linalg.norm(ls - lr, axis=1).mean() / iod), 4)
            row["identity"] = round(float(np.dot(emb(fr_), ref)), 4)
            row["source_identity"] = round(float(np.dot(emb(fs_), ref)), 4)
            nme.append(row["landmark_nme"]); ids.append(row["identity"]); sids.append(row["source_identity"])
        rows.append(row)
    t = THRESHOLDS
    checks = {
        "T1_frames": len(src) == len(res),
        "T1_fps": (sfps != sfps and rfps != rfps) or abs(sfps - rfps) <= t["fps_tol"],   # NaN = frame folders
        "T2_pixels_outside_face_identical": pix_bad == 0,
        "T3_landmarks": bool(nme) and float(np.mean(nme)) <= t["landmark_nme_max"],
        "T4_identity_mean": bool(ids) and float(np.mean(ids)) >= t["identity_mean_min"],
        "T4_identity_std": bool(ids) and float(np.std(ids)) <= t["identity_std_max"],
        "T4_source_identity": bool(sids) and float(np.mean(sids)) <= t["source_identity_max"],
    }
    report = {"thresholds": t, "frames": [len(src), len(res)], "fps": [sfps, rfps],
              "frames_with_pixels_changed_outside_face": int(pix_bad), "faces_measured": len(ids),
              "landmark_nme_mean": round(float(np.mean(nme)), 4) if nme else None,
              "identity_mean": round(float(np.mean(ids)), 4) if ids else None,
              "identity_std": round(float(np.std(ids)), 4) if ids else None,
              "source_identity_mean": round(float(np.mean(sids)), 4) if sids else None,
              "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL", "per_frame": rows}
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_frame"}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
