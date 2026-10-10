"""Makeup gate: does a face swap keep the TARGET's makeup (brows, eyeliner, lips, contour) while carrying the new identity?

Rule fixed before the first run (10.10, owner: «мейкап сверху … контур, макияж»):
  winner = the variant with the lowest mean dE (brows + eyes + lips) among variants whose identity is >= identity(A) - 0.05,
  where A is the plain `quality` preset. Contour = Pearson correlation of blurred skin luminance (higher = shading kept).
Regions come from the face parser (bisenet_resnet_34, CelebAMask classes) on the TARGET frame and are applied to both
clips at the same place (the animation is preserved, Gate 0/1).

Run with FaceFusion's interpreter, cwd = the FaceFusion checkout:
  .venv/Scripts/python.exe makeup_gate.py --target seg.mp4 --refs a.png,b.png --variant A=varA.mp4 --variant B=varB.mp4 --out r.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from facefusion import state_manager  # noqa: E402

for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
             "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
             "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
             "face_landmarker_score": 0.5, "log_level": "error", "face_parser_model": "bisenet_resnet_34",
             "face_occluder_model": "xseg_1"}.items():
    state_manager.init_item(k, v)
from facefusion import face_masker  # noqa: E402
from facefusion.face_creator import get_many_faces  # noqa: E402
from animation_gate import read_frames  # noqa: E402

REGIONS = {"brows": (2, 3), "eyes": (4, 5), "lips": (12, 13), "skin": (1,)}
IDENTITY_SLACK = 0.05


def largest(fs):
    return max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0]) if fs else None


def emb(face):
    e = getattr(face, "embedding_norm", None)
    return e if e is not None else face.embedding / np.linalg.norm(face.embedding)


def parse_regions(frame, face) -> dict[str, np.ndarray]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in face.bounding_box]
    cx, cy, s = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 1.6
    bx1, by1, bx2, by2 = int(max(0, cx - s / 2)), int(max(0, cy - s / 2)), int(min(w, cx + s / 2)), int(min(h, cy + s / 2))
    crop = frame[by1:by2, bx1:bx2]
    prep = cv2.resize(crop, (512, 512))[:, :, ::-1].astype(np.float32) / 255.0
    prep = (prep - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    labels = face_masker.forward_parse_face(prep.transpose(2, 0, 1)[None]).argmax(0).astype(np.uint8)
    labels = cv2.resize(labels, (bx2 - bx1, by2 - by1), interpolation=cv2.INTER_NEAREST)
    out = {}
    for name, ids in REGIONS.items():
        m = np.zeros((h, w), bool)
        m[by1:by2, bx1:bx2] = np.isin(labels, ids)
        out[name] = m
    return out


def measure(target, result, ref, masks_cache) -> dict:
    de = {k: [] for k in REGIONS}
    contour, ids = [], []
    for i, (t, r) in enumerate(zip(target, result)):
        if i not in masks_cache:
            f = largest(get_many_faces([t]))
            masks_cache[i] = parse_regions(t, f) if f is not None else None
        regions = masks_cache[i]
        fr = largest(get_many_faces([r]))
        if fr is not None:
            ids.append(float(np.dot(emb(fr), ref)))
        if regions is None:
            continue
        lt = cv2.cvtColor(t, cv2.COLOR_BGR2LAB).astype(np.float32)
        lr = cv2.cvtColor(r, cv2.COLOR_BGR2LAB).astype(np.float32)
        for name, m in regions.items():
            if m.sum() >= 30:
                de[name].append(float(np.linalg.norm(lt[m] - lr[m], axis=1).mean()))
        sk = regions["skin"]
        if sk.sum() >= 200:
            bt = cv2.GaussianBlur(lt[:, :, 0], (0, 0), 6)[sk]
            br = cv2.GaussianBlur(lr[:, :, 0], (0, 0), 6)[sk]
            if bt.std() > 0 and br.std() > 0:
                contour.append(float(np.corrcoef(bt, br)[0, 1]))
    makeup = [np.mean(de[k]) for k in ("brows", "eyes", "lips") if de[k]]
    return {"identity_mean": round(float(np.mean(ids)), 4) if ids else None, "faces": len(ids),
            "dE": {k: round(float(np.mean(v)), 2) if v else None for k, v in de.items()},
            "makeup_dE_mean": round(float(np.mean(makeup)), 2) if makeup else None,
            "contour_corr": round(float(np.mean(contour)), 4) if contour else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--variant", action="append", required=True, help="NAME=path")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ref = np.mean([emb(largest(get_many_faces([cv2.imread(p)]))) for p in a.refs.split(",")], axis=0)
    ref /= np.linalg.norm(ref)
    target = read_frames(Path(a.target))
    cache: dict = {}
    rows = {}
    for spec in a.variant:
        name, _, path = spec.partition("=")
        rows[name] = measure(target, read_frames(Path(path)), ref, cache)
        print(name, json.dumps(rows[name]), flush=True)
    base = rows.get("A", {}).get("identity_mean")
    eligible = {n: r for n, r in rows.items() if base is None or (r["identity_mean"] or 0) >= base - IDENTITY_SLACK}
    winner = min(eligible, key=lambda n: eligible[n]["makeup_dE_mean"] if eligible[n]["makeup_dE_mean"] is not None else 1e9)
    report = {"rule": f"min makeup dE among identity >= identity(A) - {IDENTITY_SLACK}", "variants": rows,
              "eligible": sorted(eligible), "winner": winner}
    Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({"winner": winner, "eligible": sorted(eligible)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
