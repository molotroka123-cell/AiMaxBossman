"""Temporal smoothing of a generated region (Stage 2 clothes flicker, 10.10).

VACE repaints every frame independently, so the garment shimmers (v2: +3.56 dE frame-to-frame over the source, limit
+2.0). Inside the hole only: out_t = A * cur_t + (1 - A) * warp(out_{t-1}), warp = Farneback flow of the SOURCE frames
(true motion). Outside the hole every pixel stays the generated/composited frame, so Stage 2 T2 (0 changed pixels
outside) is untouched. Measured on the 81-frame clip: flicker +3.56 -> +0.48, outfit dE 13.0 -> 13.2.

  python temporal_smooth.py --src source.mp4 --result master.mp4 --holes hole_dir --out smoothed.mp4 [--alpha 0.5]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np


def blend_step(cur: np.ndarray, warped_prev: np.ndarray, hole: np.ndarray, alpha: float) -> np.ndarray:
    """One smoothing step: mix `cur` with the motion-compensated previous output, only where `hole` is True."""
    mixed = alpha * cur.astype(np.float32) + (1.0 - alpha) * warped_prev.astype(np.float32)
    return np.where(hole[..., None], mixed.round().astype(np.uint8), cur)


def main(argv=None) -> int:
    import cv2
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--holes", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--alpha", type=float, default=0.5)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "video_gate"))
    import animation_gate as ag
    src, res = ag.read_frames(a.src), ag.read_frames(a.result)
    n = min(len(src), len(res))
    h, w = res[0].shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    rate = ag.probe(a.result)["fps"]
    enc = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{w}x{h}", "-r", str(rate), "-i", "-", "-c:v", "libx264rgb", "-qp", "0", str(a.out)],
                           stdin=subprocess.PIPE)
    prev = None
    for i in range(n):
        hole = cv2.imread(str(a.holes / f"{i + 1:04d}.png"), 0) > 127
        out = res[i]
        if prev is not None:
            flow = cv2.calcOpticalFlowFarneback(cv2.cvtColor(src[i], cv2.COLOR_BGR2GRAY),
                                                cv2.cvtColor(src[i - 1], cv2.COLOR_BGR2GRAY), None, 0.5, 4, 21, 3, 5, 1.2, 0)
            warped = cv2.remap(prev, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            out = blend_step(res[i], warped, hole, a.alpha)
        enc.stdin.write(np.ascontiguousarray(out).tobytes())
        prev = out
    enc.stdin.close()
    return enc.wait()


if __name__ == "__main__":
    sys.exit(main())
