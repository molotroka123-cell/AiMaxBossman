"""Masks for the Genjutsu live constructor, computed locally with FaceFusion's own models.

Run ONLY with FaceFusion's interpreter (it imports `facefusion`, not `bcc`), with cwd = the FaceFusion checkout:
    <facefusion>/.venv/Scripts/python.exe masks_worker.py <image> <out_dir>
Writes person.png, clothes.png, top.png, bottom.png, hair.png (8-bit, 255 = region) and masks.json (areas, timing).

- person:  background_remover u2net_human
- clothes: person minus hair minus skin (YCrCb skin range); u2net_cloth returned ~0% on real frames (10.10)
- hair:    bisenet face parser on a widened head crop (CelebAMask class 17), extended down long hair by colour
           (Lab Mahalanobis to the detected hair pixels), always inside the person mask
- top/bottom: the clothes mask split at the person's vertical middle — an APPROXIMATION, labelled as such
"""
from __future__ import annotations

import json
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
             "face_landmarker_score": 0.5, "log_level": "error", "face_parser_model": "bisenet_resnet_34", "face_occluder_model": "xseg_1",
             "background_remover_model": "u2net_human", "background_remover_fill_color": [0, 0, 0, 0],
             "background_remover_despill_color": [0, 0, 0, 0]}.items():
    state_manager.init_item(k, v)

from facefusion import face_masker  # noqa: E402
from facefusion.face_creator import get_many_faces  # noqa: E402
from facefusion.processors.modules.background_remover import core as bgr  # noqa: E402

HAIR = 17


def remover_mask(frame, model: str):
    state_manager.init_item("background_remover_model", model)
    bgr.clear_inference_pool()
    if not bgr.pre_check():                       # downloads/validates the model once, then the pool can load it
        raise RuntimeError(f"model {model} unavailable")
    _, mask = bgr.remove_background(frame)
    mask = np.asarray(mask, dtype=np.float32)
    if mask.max() > 1.0:
        mask = mask / 255.0
    return (mask > 0.5).astype(np.uint8) * 255


def hair_mask(frame, person, skin):
    h, w = frame.shape[:2]
    out = np.zeros((h, w), np.uint8)
    bottoms = []
    face_masker.pre_check()
    for f in get_many_faces([frame]):
        x1, y1, x2, y2 = [float(v) for v in f.bounding_box]
        cx, cy, s = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 2.2
        bx1, by1 = int(max(0, cx - s / 2)), int(max(0, cy - s * 0.6))
        bx2, by2 = int(min(w, cx + s / 2)), int(min(h, cy + s * 0.4))
        crop = frame[by1:by2, bx1:bx2]
        if crop.size == 0:
            continue
        prep = cv2.resize(crop, (512, 512))[:, :, ::-1].astype(np.float32) / 255.0
        prep = (prep - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        labels = face_masker.forward_parse_face(prep.transpose(2, 0, 1)[None]).argmax(0).astype(np.uint8)
        part = cv2.resize((labels == HAIR).astype(np.uint8) * 255, (bx2 - bx1, by2 - by1), interpolation=cv2.INTER_NEAREST)
        out[by1:by2, bx1:bx2] = np.maximum(out[by1:by2, bx1:bx2], part)
        bottoms.append((by2, bx1, bx2))
    if out.any():                                     # long hair below the head crop
        # only in columns where strands reach the bottom of the head crops, colour-close, inside the person, connected
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        seed = lab[out.reshape(-1) > 0]
        mu, cov = seed.mean(0), np.cov(seed.T) + np.eye(3) * 4.0
        d = lab - mu
        m2 = np.einsum("ij,jk,ik->i", d, np.linalg.inv(cov), d).reshape(h, w)
        cols = np.zeros(w, bool)
        for by2_, bx1_, bx2_ in bottoms:
            band = out[max(0, by2_ - max(4, (by2_ // 20))):by2_, bx1_:bx2_]
            cols[bx1_:bx2_] |= band.any(axis=0)
        cols = cv2.dilate(cols.astype(np.uint8)[None], np.ones((1, 15), np.uint8))[0].astype(bool)
        allow = np.zeros((h, w), bool)
        allow[:, cols] = True
        cand = ((m2 < 4.0) & (person > 0) & allow & (skin == 0)).astype(np.uint8) * 255
        _, lbl = cv2.connectedComponents(cv2.bitwise_or(cand, out))
        keep = np.unique(lbl[out > 0])
        out = np.isin(lbl, keep[keep > 0]).astype(np.uint8) * 255
    return cv2.morphologyEx(out, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) & person


def main() -> int:
    src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = cv2.imread(str(src))
    if frame is None:
        print(json.dumps({"error": "image_unreadable"}))
        return 2
    t0 = time.time()
    person = remover_mask(frame, "u2net_human")
    ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycc, (0, 135, 85), (255, 180, 135)) & person
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    hair = hair_mask(frame, person, skin)
    clothes = person & cv2.bitwise_not(hair) & cv2.bitwise_not(cv2.dilate(skin, np.ones((7, 7), np.uint8)))
    clothes = cv2.morphologyEx(clothes, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    ys = np.where(person.any(axis=1))[0]
    mid = int((ys[0] + ys[-1]) / 2) if len(ys) else frame.shape[0] // 2
    top, bottom = clothes.copy(), clothes.copy()
    top[mid:] = 0
    bottom[:mid] = 0
    masks = {"person": person, "clothes": clothes, "top": top, "bottom": bottom, "hair": hair}
    for name, m in masks.items():
        cv2.imwrite(str(out_dir / f"{name}.png"), m)
    info = {"seconds": round(time.time() - t0, 2), "size": [int(frame.shape[1]), int(frame.shape[0])],
            "area": {k: round(float((v > 0).mean()), 4) for k, v in masks.items()},
            "approximate": ["top", "bottom"], "models": {"person": "u2net_human", "clothes": "person - hair - skin (YCrCb)",
                                                        "hair": "bisenet_resnet_34 + colour extension"}}
    (out_dir / "masks.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
