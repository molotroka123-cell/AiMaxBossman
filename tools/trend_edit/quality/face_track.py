"""Per-frame face box + 5 landmarks (+ ArcFace identity to her refs) for source clips: the data for match cuts.

Run with FaceFusion's interpreter, cwd = the FaceFusion checkout (same bootstrap as face_gate.py):
  cd <facefusion> && .venv/Scripts/python.exe <repo>/tools/trend_edit/quality/face_track.py --video v.mp4 --refs a,b --out t.json [--step 1]

Output: {"fps", "w", "h", "frames": {frame_index: {"box": [x1,y1,x2,y2], "lm5": [[x,y]*5], "id": float}}}
lm5 order (FaceFusion / InsightFace): right-eye, left-eye, nose, right-mouth, left-mouth (image left to right).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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


def emb(face):
    e = getattr(face, "embedding_norm", None)
    return e if e is not None else face.embedding / np.linalg.norm(face.embedding)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, default=1e9)
    ap.add_argument("--crop", default="", help="x,y,w,h applied before detection (coordinates stay in the CROPPED frame)")
    a = ap.parse_args()
    refs = []
    for p in a.refs.split(","):
        img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        fs = get_many_faces([img]) if img is not None else []
        if fs:
            refs.append(emb(max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0])))
    ref = np.mean(refs, axis=0)
    ref /= np.linalg.norm(ref)
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                         "-of", "csv=p=0", a.video], capture_output=True, text=True).stdout.strip().split(",")
    w, h = int(pr[0]), int(pr[1])
    num, _, den = pr[2].partition("/")
    fps = float(num) / float(den or 1)
    cx = [int(v) for v in a.crop.split(",")] if a.crop else None
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", a.video, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    out, i = {}, 0
    while True:
        buf = proc.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        t = i / fps
        if i % a.step == 0 and a.t0 <= t <= a.t1:
            fr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            if cx:
                fr = np.ascontiguousarray(fr[cx[1]:cx[1] + cx[3], cx[0]:cx[0] + cx[2]])
            fs = []
            for attempt in range(6):                       # DirectML can hang under GPU contention: retry
                try:
                    fs = get_many_faces([fr])
                    break
                except Exception as e:  # noqa: BLE001
                    print("retry", i, type(e).__name__, file=sys.stderr)
                    time.sleep(2.0)
            if fs:
                f = max(fs, key=lambda q: q.bounding_box[2] - q.bounding_box[0])
                out[i] = {"box": [float(v) for v in f.bounding_box], "lm5": np.asarray(f.landmark_set.get("5")).round(1).tolist(),
                          "id": round(float(np.dot(emb(f), ref)), 4)}
        i += 1
    Path(a.out).write_text(json.dumps({"fps": fps, "w": w if not cx else cx[2], "h": h if not cx else cx[3], "frames": out}),
                           encoding="utf-8")
    print("tracked", len(out), "of", i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
