#!/usr/bin/env python3
"""Dataset prep helper for LoRA retraining: arcface audit of a dataset + face-centred square crops of new images.

Run with the FaceFusion venv python (it provides get_many_faces):

    <facefusion-venv>/python tools/runpod/lora_face_audit.py audit --facefusion DIR --refs a.jpg,b.jpg \
        --dataset DIR_WITH_IMAGES [--drop-below 0.45]
    <facefusion-venv>/python tools/runpod/lora_face_audit.py crop  --facefusion DIR --refs a.jpg,b.jpg \
        --images "new/*.webp" --out cropdir [--side 0] [--down 0.45]

audit: for every image the largest face is compared with (a) the mean of the real reference faces and (b) the mean of
       the dataset itself; images whose cosine to the dataset mean is below --drop-below are listed as outliers.
crop:  RGB png, square (side = image width by default), face centred horizontally where possible and placed so the
       torso is included (--down = fraction of the side above the face centre), resized to 1024x1024; also prints the
       arcface cosine of the source image to the reference mean (generated variants of her: 0.55-0.65 is usable,
       0.54-0.56 is visible identity drift).
Images are never modified in place and never leave the machine.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lora_eval import load_facefusion  # noqa: E402


def _read(path):
    import cv2
    from PIL import Image
    if path.lower().endswith(".webp"):
        return cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
    return cv2.imread(path)


def _faces(embs_fn, img):
    from facefusion.face_creator import get_many_faces
    out = []
    for f in get_many_faces([img]):
        e = getattr(f, "embedding_norm", None)
        if e is None:
            e = f.embedding / np.linalg.norm(f.embedding)
        out.append((np.asarray(e, dtype=np.float32), [float(v) for v in f.bounding_box]))
    out.sort(key=lambda x: -(x[1][2] - x[1][0]))     # largest face first
    return out


def _ref_mean(a, embs):
    es = []
    for p in a.refs.split(","):
        fs = _faces(embs, _read(p))
        if fs:
            es.append(fs[0][0])
    c = np.mean(es, axis=0)
    return c / np.linalg.norm(c)


def audit(a, embs):
    c = _ref_mean(a, embs)
    rows = []
    for p in sorted(glob.glob(os.path.join(a.dataset, "**", "*.*"), recursive=True)):
        if not p.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        fs = _faces(embs, _read(p))
        rows.append({"path": p, "e": fs[0][0] if fs else None, "n_faces": len(fs)})
    E = np.array([r["e"] for r in rows if r["e"] is not None])
    m = E.mean(0)
    m /= np.linalg.norm(m)
    out = []
    for r in rows:
        e = r.pop("e")
        r["cos_real_refs"] = None if e is None else round(float(e @ c), 3)
        r["cos_dataset_mean"] = None if e is None else round(float(e @ m), 3)
        out.append(r)
    out.sort(key=lambda r: (r["cos_dataset_mean"] is None, r["cos_dataset_mean"] or 0))
    bad = [r for r in out if r["cos_dataset_mean"] is not None and r["cos_dataset_mean"] < a.drop_below]
    print(json.dumps({"n": len(out), "no_face": [r["path"] for r in out if r["cos_dataset_mean"] is None],
                      "outliers_below_%.2f" % a.drop_below: [r["path"] for r in bad],
                      "lowest5": out[:5]}, indent=1))


def crop(a, embs):
    import cv2
    c = _ref_mean(a, embs)
    os.makedirs(a.out, exist_ok=True)
    for p in sorted(glob.glob(a.images)):
        img = _read(p)
        fs = _faces(embs, img)
        if not fs:
            print("NO FACE", p)
            continue
        e, (x1, y1, x2, y2) = fs[0]
        h, w = img.shape[:2]
        side = int(a.side) or min(w, h)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        X = int(np.clip(cx - side / 2, 0, w - side))
        Y = int(np.clip(cy - a.down * side, 0, h - side))
        sq = cv2.resize(img[Y:Y + side, X:X + side], (1024, 1024), interpolation=cv2.INTER_LANCZOS4)
        dst = os.path.join(a.out, os.path.splitext(os.path.basename(p))[0] + "_crop.png")
        cv2.imwrite(dst, sq)
        print(json.dumps({"src": os.path.basename(p), "cos_real_refs": round(float(e @ c), 3), "crop_y": Y, "out": dst}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("audit", "crop"):
        s = sub.add_parser(n)
        s.add_argument("--facefusion", required=True)
        s.add_argument("--refs", required=True, help="comma separated real reference photos")
    sub.choices["audit"].add_argument("--dataset", required=True)
    sub.choices["audit"].add_argument("--drop-below", type=float, default=0.45)
    sub.choices["crop"].add_argument("--images", required=True, help="glob")
    sub.choices["crop"].add_argument("--out", required=True)
    sub.choices["crop"].add_argument("--side", type=int, default=0)
    sub.choices["crop"].add_argument("--down", type=float, default=0.45)
    a = ap.parse_args()
    embs = load_facefusion(a.facefusion)
    {"audit": audit, "crop": crop}[a.cmd](a, embs)


if __name__ == "__main__":
    main()
