"""Face identity helpers on FaceFusion's own ArcFace (run with <facefusion>/.venv python, DirectML or CPU).

  pick   choose the N best reference photos of ONE person from folders: big, sharp, frontal, consistent with the median
         identity, no near-duplicates (the gf-refs-all/pick.py recipe, generalised)
  score  mean ArcFace cosine between the face in a clip's frames and the mean embedding of reference photos

  python face_id.py pick --dir D1 --dir D2 --n 5 --out picked.json
  python face_id.py score --refs a.jpg,b.jpg --clip swapped.mp4 [--step 4] [--out score.json]

Cosine is the same measure the Gate 1 reports use; it is a similarity of embeddings, not a verdict about a person.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

FACEFUSION = r"C:\Users\asd\Bossman\apps-local\facefusion"
_ready = False


def _init():
    global _ready
    if _ready:
        return
    sys.path.insert(0, FACEFUSION)
    from facefusion import state_manager
    import os
    for k, v in {"execution_providers": [os.environ.get("FACE_ID_PROVIDER", "directml")], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error", "face_selector_mode": "many"}.items():
        state_manager.init_item(k, v)
    _ready = True


def faces(frame):
    _init()
    from facefusion.face_creator import get_many_faces
    return get_many_faces([frame])


def emb(face) -> np.ndarray:
    e = getattr(face, "embedding_norm", None)
    if e is None:
        e = face.embedding / np.linalg.norm(face.embedding)
    return np.asarray(e, np.float32)


def largest(fs):
    return max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0])


def cmd_pick(a) -> int:
    paths: list[str] = []
    for d in a.dir:
        for ext in ("jpg", "jpeg", "png"):
            paths += sorted(glob.glob(str(Path(d) / f"*.{ext}")))
    rows = []
    for p in paths:
        img = cv2.imread(p)
        fs = faces(img) if img is not None else []
        if not fs:
            continue
        f = largest(fs)
        x1, y1, x2, y2 = [int(v) for v in f.bounding_box]
        crop = cv2.cvtColor(img[max(0, y1):y2, max(0, x1):x2], cv2.COLOR_BGR2GRAY)
        if crop.size == 0:
            continue
        sharp = float(cv2.Laplacian(cv2.resize(crop, (256, 256)), cv2.CV_64F).var())
        lm = f.landmark_set.get("5") if hasattr(f, "landmark_set") else None
        yaw = 0.0
        if lm is not None:
            le, re_, nose = lm[0], lm[1], lm[2]
            yaw = abs(nose[0] - (le[0] + re_[0]) / 2) / (abs(re_[0] - le[0]) + 1e-6)
        rows.append({"path": p, "size": x2 - x1, "sharp": sharp, "yaw": round(float(yaw), 3), "emb": emb(f), "faces": len(fs)})
    if not rows:
        print("no faces found")
        return 2
    E = np.array([r["emb"] for r in rows])
    center = np.median(E, axis=0)
    center /= np.linalg.norm(center)
    for r in rows:
        r["consist"] = float(np.dot(r["emb"], center))
        r["score"] = r["consist"] * 2 + min(r["size"], 400) / 400 + min(r["sharp"], 600) / 600 - r["yaw"] * 1.5
    rows.sort(key=lambda r: -r["score"])
    picked = []
    for r in rows:
        if r["consist"] < a.min_consist or r["faces"] > 1 and not a.allow_multi:
            continue
        if any(float(np.dot(r["emb"], q["emb"])) > 0.93 for q in picked):
            continue
        src_key = re.sub(r"[-_]\d+$", "", Path(r["path"]).stem)       # frames of one video share a key
        if sum(1 for q in picked if q["src"] == src_key) >= a.per_source:
            continue
        r["src"] = src_key
        picked.append(r)
        if len(picked) == a.n:
            break
    for r in picked:
        print(f"{Path(r['path']).name:28s} consist={r['consist']:.3f} size={r['size']} sharp={r['sharp']:.0f} yaw={r['yaw']}")
    Path(a.out).write_text(json.dumps([{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k != "emb"}
                                       for r in picked], indent=1), encoding="utf-8")
    print("candidates with a face:", len(rows), "picked:", len(picked))
    return 0


def cmd_score(a) -> int:
    refs = [cv2.imread(p) for p in a.refs.split(",")]
    embs = [emb(largest(faces(r))) for r in refs if r is not None and faces(r)]
    ref = np.mean(embs, axis=0)
    ref /= np.linalg.norm(ref)
    per_ref = {}
    cap = cv2.VideoCapture(a.clip)
    sims, i = [], 0
    per_frame: list = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % a.step == 0:
            fs = faces(fr)
            if fs:
                sims.append(float(np.dot(emb(largest(fs)), ref)))
                per_frame.append(round(sims[-1], 3))
            else:
                per_frame.append(None)
        i += 1
    for p, e in zip(a.refs.split(","), embs):
        per_ref[Path(p).name] = round(float(np.dot(e, ref)), 3)
    res = {"clip": a.clip, "frames_scored": len(sims), "frames": i, "identity_mean": round(float(np.mean(sims)), 4) if sims else None,
           "identity_min": round(float(np.min(sims)), 4) if sims else None,
           "identity_std": round(float(np.std(sims)), 4) if sims else None, "refs_to_mean": per_ref,
           "step": a.step, "per_frame": per_frame}
    print(json.dumps({k: v for k, v in res.items() if k != "per_frame"}, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--dir", action="append", required=True)
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--min-consist", type=float, default=0.45)
    p.add_argument("--per-source", type=int, default=2, help="max photos from one video / photo series")
    p.add_argument("--allow-multi", action="store_true", help="accept photos with several faces (the largest is used)")
    p.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("--refs", required=True)
    s.add_argument("--clip", required=True)
    s.add_argument("--step", type=int, default=4)
    s.add_argument("--out")
    a = ap.parse_args(argv)
    return cmd_pick(a) if a.cmd == "pick" else cmd_score(a)


if __name__ == "__main__":
    sys.exit(main())
