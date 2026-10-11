"""Best-effort front-view hair matte (11.10, gate FAIL, improved): SegFormer hair x dilated SAM2.1-tiny object mask,
+4 px growth, guided-filter edge snap, soft alpha. Measured on the owner's reference frame against the GT mask:
IoU 0.806 -> 0.860, boundary F1@3px 0.46 -> 0.655 (gate 0.90 / 0.80 NOT met; see STATE.md section 7-8).

CPU only on purpose (the GPU may be busy): SegFormer on CPU ~1 s, SAM2.1-tiny encoder on CPU ~1.5 s per frame.
Licences: SegFormer-B2 clothes NVIDIA non-commercial, SAM2 Apache-2.0 -> personal edits only.

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_fused.py --src clip.mp4 --first 72 --count 50 --out DIR [--crop 280:1000]
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center" / "bcc" / "direct_gen"))
sys.path.insert(0, str(ROOT / "tools" / "genjutsu"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parsing_worker as pw  # noqa: E402
import hair_v2 as hv  # noqa: E402


def low_priority() -> None:
    try:
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL
    except Exception:
        pass


class Fused:
    def __init__(self, provider: str = "cpu"):
        import genjutsu as gj
        self.parser = pw.Parser(hv.SEG_MODEL, provider)
        self.seg = gj.Segmenter()
        self.rng = np.random.default_rng(3)

    def _points(self, mask, n, erode, x0, y0, S):
        mk = cv2.erode((mask > 0.9).astype(np.uint8), np.ones((erode, erode), np.uint8))[y0:y0 + S, x0:x0 + S]
        yy, xx = np.where(mk > 0)
        if len(yy) == 0:
            return []
        idx = self.rng.choice(len(yy), min(n, len(yy)), replace=False)
        return [(int(xx[i]), int(yy[i])) for i in idx]

    def __call__(self, frame: np.ndarray) -> dict:
        """frame BGR (the picture region, no pillar bars). Returns {alpha, hair, sam} float32 0..1 at frame size."""
        h, w = frame.shape[:2]
        m, _ = pw.parse(frame, self.parser)
        hair = m["hair"].astype(np.float32) / 255.0
        skin = m["skin"].astype(np.float32) / 255.0
        clothes = m["clothes"].astype(np.float32) / 255.0
        person = m["person"].astype(np.float32) / 255.0
        excl = 1.0 - hv.smoothstep(0.08, 0.45, cv2.GaussianBlur(skin, (0, 0), 1.2))
        base = hair * excl
        ys, xs = np.where(hair > 0.5)
        if len(ys) < 200:
            return {"alpha": np.zeros((h, w), np.float32), "hair": base, "sam": np.zeros((h, w), np.float32)}
        S = min(h, w)
        y0 = int(np.clip((ys.min() + ys.max()) // 2 - S // 2, 0, h - S))
        x0 = int(np.clip((xs.min() + xs.max()) // 2 - S // 2, 0, w - S))
        P = [(x, y, 1) for x, y in self._points(hair, 6, 9, x0, y0, S)]
        N = [(x, y, 0) for x, y in self._points(skin, 4, 5, x0, y0, S) + self._points(clothes, 4, 9, x0, y0, S)]
        N += [(x, y, 0) for x, y in self._points((person < 0.05).astype(np.float32), 3, 15, x0, y0, S)]
        crop = frame[y0:y0 + S, x0:x0 + S]
        x_in = ((cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), (1024, 1024), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255
                 - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
        e0, e1, e2 = self.seg.enc.run(None, {"pixel_values": x_in})
        sc = 1024.0 / S
        bx = [max(0, xs.min() - 10 - x0), max(0, ys.min() - 10 - y0), min(S - 1, xs.max() + 10 - x0), min(S - 1, ys.max() + 10 - y0)]
        pts = np.array([[[[px * sc, py * sc] for px, py, _ in P + N]]], np.float32)
        lab = np.array([[[l for _, _, l in P + N]]], np.int64)
        b = np.array([[[bx[0] * sc, bx[1] * sc, bx[2] * sc, bx[3] * sc]]], np.float32)
        iou, masks, _ = self.seg.dec.run(None, {"input_points": pts, "input_labels": lab, "input_boxes": b,
                                                "image_embeddings.0": e0, "image_embeddings.1": e1, "image_embeddings.2": e2})
        # candidate that best agrees with the SegFormer hair (the 3 candidates are whole / part / sub-part masks)
        best, best_s = None, -1.0
        for k in range(masks.shape[2]):
            up = cv2.resize(masks[0, 0, k].astype(np.float32), (S, S), interpolation=cv2.INTER_LINEAR) > 0
            full = np.zeros((h, w), np.float32)
            full[y0:y0 + S, x0:x0 + S] = up
            inter = float((full * (base > 0.5)).sum())
            uni = float(((full > 0.5) | (base > 0.5)).sum())
            s = inter / max(1.0, uni)
            if s > best_s:
                best, best_s = full, s
        sam = best
        sam_d = cv2.dilate(sam, np.ones((9, 9), np.uint8))
        inter = base * sam_d
        grow = cv2.dilate((inter > 0.5).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(np.float32)
        guide = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        sn = pw.guided_filter(guide, grow * excl, 8, 1e-4)
        alpha = np.clip((sn - 0.15) / 0.30, 0, 1)         # 0.30 is the measured binarisation threshold -> alpha 0.5
        return {"alpha": alpha.astype(np.float32), "hair": base, "sam": sam, "skin": skin, "person": person}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--crop", default="", help="x0:x1 columns of the picture region (pillar bars removed)")
    a = ap.parse_args()
    low_priority()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(a.src)
    cap.set(cv2.CAP_PROP_POS_FRAMES, a.first)
    f = Fused("cpu")
    x0, x1 = (int(v) for v in a.crop.split(":")) if a.crop else (0, None)
    t0 = time.time()
    for i in range(a.count):
        idx = a.first + i
        p = out / f"{idx:05d}.npy"
        ok, fr = cap.read()
        if not ok:
            break
        if p.exists():
            continue
        fr = np.ascontiguousarray(fr[:, x0:x1])
        r = f(fr)
        np.save(p, np.round(r["alpha"] * 255).astype(np.uint8))
        if i % 10 == 9:
            print(f"{i + 1}/{a.count} {time.time() - t0:.0f}s", flush=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
