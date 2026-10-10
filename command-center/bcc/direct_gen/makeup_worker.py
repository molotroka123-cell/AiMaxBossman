"""Makeup layer over a face swap: carry the TARGET's makeup (eyeliner, drawn brows, lipstick) onto the swapped face.

Measured 10.10 (makeup gate, owner face on a made-up target): a plain swap keeps the new identity (0.749) but loses the
makeup (brows dE 29, eyes 39); keeping the target's brows/eyes keeps the makeup (7 / 12) but loses identity (0.582).
This layer keeps the swapped geometry and transfers only what makeup adds:
  * brows + eyes (dilated, so the liner on the lids is included): L = min(L_swap, L_target) — the darker strokes win;
  * lips: a/b (colour) from the target, L from the swap;
  all inside soft masks from the face parser (bisenet, CelebAMask classes) on the TARGET frame.

Run ONLY with FaceFusion's interpreter, cwd = the FaceFusion checkout:
    <facefusion>/.venv/Scripts/python.exe makeup_worker.py target.mp4 swapped.mp4 out.mp4
Writes out.mp4 (libx264rgb -qp 0, RGB-lossless) and prints {"frames": n, "made_up": k}.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

BROWS, EYES, LIPS = (2, 3), (4, 5), (12, 13)


def soft(mask, grow: int, blur: float):
    import cv2
    import numpy as np
    m = mask.astype(np.uint8) * 255
    if grow:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
    return cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), blur) if blur else m.astype(np.float32) / 255.0


def apply_makeup(target_bgr, swapped_bgr, labels_full, scale: float):
    """labels_full: parser labels at frame size (0 = background). scale = face size in px / 100 for mask growth."""
    import cv2
    import numpy as np
    lt = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    ls = cv2.cvtColor(swapped_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    out = ls.copy()
    eye_brow = soft(np.isin(labels_full, BROWS + EYES), grow=max(2, int(4 * scale)), blur=max(1.0, 1.5 * scale))
    out[:, :, 0] = ls[:, :, 0] * (1 - eye_brow) + np.minimum(ls[:, :, 0], lt[:, :, 0]) * eye_brow
    lips = soft(np.isin(labels_full, LIPS), grow=max(1, int(1 * scale)), blur=max(1.0, 1.0 * scale))
    for c in (1, 2):
        out[:, :, c] = ls[:, :, c] * (1 - lips) + lt[:, :, c] * lips
    return cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def main(argv: list[str]) -> int:
    import cv2
    import numpy as np
    target, swapped, dst = argv[:3]
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error", "face_parser_model": "bisenet_resnet_34",
                 "face_occluder_model": "xseg_1"}.items():
        state_manager.init_item(k, v)
    from facefusion import face_masker
    from facefusion.face_creator import get_many_faces
    face_masker.pre_check()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pose_gate_worker import _frames, _probe

    w, h, rate = _probe(target)
    enc = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{w}x{h}", "-r", rate, "-i", "-", "-c:v", "libx264rgb", "-qp", "0", "-preset", "ultrafast", dst],
                           stdin=subprocess.PIPE)
    n = done = 0
    for t, s in zip(_frames(target, w, h), _frames(swapped, w, h)):
        n += 1
        out = s
        faces = get_many_faces([t])
        if faces:
            f = max(faces, key=lambda x: x.bounding_box[2] - x.bounding_box[0])
            x1, y1, x2, y2 = [float(v) for v in f.bounding_box]
            cx, cy, side = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1)
            sz = side * 1.6
            bx1, by1 = int(max(0, cx - sz / 2)), int(max(0, cy - sz / 2))
            bx2, by2 = int(min(w, cx + sz / 2)), int(min(h, cy + sz / 2))
            prep = cv2.resize(t[by1:by2, bx1:bx2], (512, 512))[:, :, ::-1].astype(np.float32) / 255.0
            prep = (prep - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
            lab = face_masker.forward_parse_face(prep.transpose(2, 0, 1)[None]).argmax(0).astype(np.uint8)
            full = np.zeros((h, w), np.uint8)
            full[by1:by2, bx1:bx2] = cv2.resize(lab, (bx2 - bx1, by2 - by1), interpolation=cv2.INTER_NEAREST)
            out = apply_makeup(t, s, full, side / 100.0)
            done += 1
        enc.stdin.write(np.ascontiguousarray(out).tobytes())
    enc.stdin.close()
    if enc.wait() != 0 or not n:
        print(json.dumps({"error": "encode_failed" if n else "no_frames"}))
        return 2
    print(json.dumps({"frames": n, "made_up": done}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
