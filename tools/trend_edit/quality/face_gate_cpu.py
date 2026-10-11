"""Per-frame face gate for a finished video: arcface to HER real photos, lost-face frames, identity / luma jitter.

Run with FaceFusion's interpreter, cwd = the FaceFusion checkout (same bootstrap as tools/video_gate/gate1_faceswap.py):

  cd <facefusion> && .venv/Scripts/python.exe <repo>/tools/trend_edit/face_gate.py --video v.mp4 --refs a.jpg,b.jpg --out gate.json [--step 1]

identity = cosine(arcface(frame face), mean arcface of the refs); also reported against the best single ref.
lost = frames with no detectable face (title cards / transitions count as lost: they are reported, not hidden).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.cwd()))
from facefusion import state_manager  # noqa: E402

for k, v in {"execution_providers": ["cpu"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
             "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
             "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
             "face_landmarker_score": 0.5, "log_level": "error"}.items():
    state_manager.init_item(k, v)
from facefusion.face_creator import get_many_faces  # noqa: E402


def main_face(frame):
    fs = get_many_faces([frame])
    return max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0]) if fs else None


def emb(face):
    e = getattr(face, "embedding_norm", None)
    return e if e is not None else face.embedding / np.linalg.norm(face.embedding)


def decode(path: str, step: int):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    w, h = int(p[0]), int(p[1])
    num, _, den = p[2].partition("/")
    fps = float(num) / float(den or 1)
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    i = 0
    while True:
        buf = proc.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        if i % step == 0:
            yield i, np.frombuffer(buf, np.uint8).reshape(h, w, 3), fps
        i += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--min", type=float, default=0.59)
    a = ap.parse_args()
    refs = []
    for p in a.refs.split(","):
        img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        f = main_face(img) if img is not None else None
        if f is not None:
            refs.append(emb(f))
    ref = np.mean(refs, axis=0)
    ref /= np.linalg.norm(ref)
    rows, ids, best, lum, fps = [], [], [], [], 25.0
    for i, fr, fps in decode(a.video, a.step):
        f = main_face(fr)
        if f is None:
            rows.append({"f": i, "face": False})
            continue
        e = emb(f)
        x1, y1, x2, y2 = [int(v) for v in f.bounding_box]
        crop = cv2.cvtColor(fr[max(0, y1):max(y1 + 1, y2), max(0, x1):max(x1 + 1, x2)], cv2.COLOR_BGR2GRAY)
        l = float(crop.mean()) if crop.size else 0.0
        idn = float(np.dot(e, ref))
        bst = float(max(np.dot(e, r) for r in refs))
        rows.append({"f": i, "face": True, "id": round(idn, 4), "best": round(bst, 4), "luma": round(l, 1),
                     "face_w": x2 - x1})
        ids.append(idn); best.append(bst); lum.append(l)
    n = len(rows)
    lost = sum(1 for r in rows if not r["face"])
    below = sum(1 for r in rows if r["face"] and r["id"] < a.min)
    ids_a = np.array(ids) if ids else np.array([0.0])
    res = {"video": a.video, "frames_checked": n, "step": a.step, "fps": fps, "refs_used": len(refs),
           "lost_face_frames": lost, "lost_face_share": round(lost / max(1, n), 4),
           "identity_mean": round(float(ids_a.mean()), 4), "identity_min": round(float(ids_a.min()), 4),
           "identity_p10": round(float(np.percentile(ids_a, 10)), 4),
           "best_ref_mean": round(float(np.mean(best)) if best else 0.0, 4),
           "frames_below_min": below, "below_min_share": round(below / max(1, n), 4), "min_threshold": a.min,
           "identity_jitter": round(float(np.abs(np.diff(ids_a)).mean()) if len(ids_a) > 1 else 0.0, 4),
           "face_luma_jitter": round(float(np.abs(np.diff(lum)).mean()) if len(lum) > 1 else 0.0, 2)}
    Path(a.out).write_text(json.dumps({"summary": res, "frames": rows}), encoding="utf-8")
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
