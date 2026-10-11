"""Best automatic hair mask so far (11.10 bench: GT IoU 0.860, boundary F1@3 0.655 - gate 0.90 / 0.80 NOT met):

    SegFormer-B2 hair  x  SAM2.1-tiny (point + box prompts from the SegFormer regions)  -> grow 4 px -> guided filter r8, thr 0.3

CPU only (onnxruntime CPUExecutionProvider, no DirectML), so it can run beside a GPU job. SegFormer-B2-clothes is a
non-commercial licence: personal edits only (same flag as parse_masks.py).

Library:   am = AutoMasker(); res = am(frame_bgr) -> dict(mask u8 0/255, hair f32, skin f32, clothes f32, person f32)
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "command-center" / "bcc" / "direct_gen"))
sys.path.insert(0, str(ROOT / "tools" / "genjutsu"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stencil_core as sc  # noqa: E402

SEG_MODEL = Path.home() / "Bossman" / "models" / "genjutsu" / "segformer_b2_clothes" / "model.onnx"
SAM_DIR = Path.home() / "Bossman" / "models" / "media" / "genjutsu-prep" / "sam2"
GROW_PX, GF_RADIUS, GF_EPS, GF_THR = 4, 8, 1e-3, 0.3          # the swept best of hair_bench_fuse_eval.py


def smoothstep(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class AutoMasker:
    def __init__(self):
        import onnxruntime as ort
        import parsing_worker as pw
        self.pw = pw
        self.parser = pw.Parser(SEG_MODEL, "cpu")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = 4          # leave cores for the GPU job's feeder processes
        self.enc = ort.InferenceSession(str(SAM_DIR / "vision_encoder.onnx"), so, providers=["CPUExecutionProvider"])
        self.dec = ort.InferenceSession(str(SAM_DIR / "prompt_encoder_mask_decoder.onnx"), so, providers=["CPUExecutionProvider"])

    # -- SegFormer regions (uint8 0..255 -> float 0..1)
    def regions(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        m, _info = self.pw.parse(frame, self.parser)
        return {k: v.astype(np.float32) / 255.0 for k, v in m.items()}

    def _pts(self, mask: np.ndarray, n: int, rng, erode: int, box) -> list[tuple[int, int]]:
        x0, y0, S = box
        mk = cv2.erode((mask > 0.9).astype(np.uint8), np.ones((erode, erode), np.uint8))[y0:y0 + S, x0:x0 + S]
        yy, xx = np.where(mk > 0)
        if len(yy) == 0:
            return []
        idx = rng.choice(len(yy), min(n, len(yy)), replace=False)
        return [(int(xx[i]), int(yy[i])) for i in idx]

    def sam_mask(self, frame: np.ndarray, reg: dict[str, np.ndarray]) -> np.ndarray:
        h, w = frame.shape[:2]
        hair, skin, clothes, person = reg["hair"], reg["skin"], reg["clothes"], reg["person"]
        ys, xs = np.where(hair > 0.5)
        if len(ys) < 50:
            return np.zeros((h, w), np.float32)
        # square crop around the hair, side >= 1.8 x its larger extent (the bench used a 1040 px crop of a 2272 px frame)
        S = int(min(min(h, w), max(256, 1.8 * max(ys.max() - ys.min(), xs.max() - xs.min()))))
        cy, cx = (ys.min() + ys.max()) // 2, (xs.min() + xs.max()) // 2
        y0 = int(np.clip(cy - S // 2, 0, h - S))
        x0 = int(np.clip(cx - S // 2, 0, w - S))
        rng = np.random.default_rng(3)
        box = (x0, y0, S)
        P = [(x, y, 1) for x, y in self._pts(hair, 10, rng, 9, box)]
        N = [(x, y, 0) for x, y in self._pts(skin, 6, rng, 5, box) + self._pts(clothes, 6, rng, 9, box)]
        N += [(x, y, 0) for x, y in self._pts((person < 0.05).astype(np.float32), 3, rng, 15, box)]
        crop = frame[y0:y0 + S, x0:x0 + S]
        rgb = cv2.cvtColor(cv2.resize(crop, (1024, 1024), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        xin = ((rgb.astype(np.float32) / 255 - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
        e0, e1, e2 = self.enc.run(None, {"pixel_values": xin})
        sc_ = 1024 / S
        pts = np.array([[[[px * sc_, py * sc_] for px, py, _ in P + N]]], np.float32)
        lab = np.array([[[l for _, _, l in P + N]]], np.int64)
        bx = [max(0, xs.min() - 10 - x0), max(0, ys.min() - 10 - y0), min(S - 1, xs.max() + 10 - x0), min(S - 1, ys.max() + 10 - y0)]
        b = np.array([[[bx[0] * sc_, bx[1] * sc_, bx[2] * sc_, bx[3] * sc_]]], np.float32)
        iou_p, masks, _ = self.dec.run(None, {"input_points": pts, "input_labels": lab, "input_boxes": b,
                                              "image_embeddings.0": e0, "image_embeddings.1": e1, "image_embeddings.2": e2})
        # no GT at run time: take the candidate that agrees best with the SegFormer hair region inside the crop
        hc = (hair[y0:y0 + S, x0:x0 + S] > 0.5)
        best, best_sc = None, -1.0
        for k in range(masks.shape[2]):
            up = cv2.resize(masks[0, 0, k].astype(np.float32), (S, S), interpolation=cv2.INTER_LINEAR) > 0
            s = sc.iou(up, hc)
            if s > best_sc:
                best, best_sc = up, s
        full = np.zeros((h, w), np.float32)
        full[y0:y0 + S, x0:x0 + S] = best
        return full

    def __call__(self, frame: np.ndarray, reg: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
        reg = reg or self.regions(frame)
        hair, skin = reg["hair"], reg["skin"]
        excl = 1.0 - smoothstep(0.08, 0.45, cv2.GaussianBlur(skin, (0, 0), 1.2))
        sam = self.sam_mask(frame, reg)
        inter = (hair * excl) * sam
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GROW_PX + 1, 2 * GROW_PX + 1))
        grown = cv2.dilate((inter > 0.5).astype(np.uint8), k).astype(np.float32)
        guide = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        sn = sc.guided_filter(guide, grown * excl, GF_RADIUS, GF_EPS)
        mask = (sn > GF_THR).astype(np.uint8) * 255
        return {"mask": mask, "hair": hair, "skin": skin, "clothes": reg["clothes"], "person": reg["person"], "sam": sam}
