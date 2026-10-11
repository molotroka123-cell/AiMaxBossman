"""Temporal stabilisation of per-frame Real-ESRGAN output.

Per-frame super-resolution hallucinates slightly different micro-texture on every frame (measured: +60..80 % temporal
difference in static areas vs bicubic). Here each restored frame is blended with the previous STABILISED frame warped by DIS
optical flow computed on the 720 px source, with a photo-consistency weight (no ghosting where the source really changed):

  out_t = w * warp(out_{t-1}) + (1 - w) * SR_t,   w = ALPHA * exp(-(|src_t - warp(src_{t-1})| / TAU)^2)

  python stabilize_sr.py --src DIR_720 --sr DIR_SR(2880) --out DIR_OUT(2160)
Frames are processed in index order; a gap in the index sequence resets the filter.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

ALPHA, TAU, S = 0.75, 8.0, 3


def run(src: Path, sr: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    names = sorted(p.stem for p in sr.glob("*.png"))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    prev_src = prev_out = None
    prev_i = None
    n = 0
    gx, gy = np.meshgrid(np.arange(720, dtype=np.float32), np.arange(720, dtype=np.float32))
    for nm in names:
        i = int(nm)
        cur_src = cv2.imread(str(src / f"{nm}.png"))
        cur_sr = cv2.imread(str(sr / f"{nm}.png"))
        cur_sr = cv2.resize(cur_sr, (720 * S, 720 * S), interpolation=cv2.INTER_AREA).astype(np.float32)
        if prev_i is None or i != prev_i + 1:
            res = cur_sr
        else:
            g1 = cv2.cvtColor(cur_src, cv2.COLOR_BGR2GRAY)
            g0 = cv2.cvtColor(prev_src, cv2.COLOR_BGR2GRAY)
            flow = dis.calc(g1, g0, None)                    # for each pixel of cur: where it was in prev
            mx, my = gx + flow[:, :, 0], gy + flow[:, :, 1]
            warped_src = cv2.remap(prev_src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            err = cv2.GaussianBlur(np.abs(cur_src.astype(np.float32) - warped_src.astype(np.float32)).mean(2), (0, 0), 1.5)
            w = ALPHA * np.exp(-((err / TAU) ** 2))
            w = cv2.resize(w, (720 * S, 720 * S), interpolation=cv2.INTER_LINEAR)[:, :, None]
            hx = cv2.resize(mx, (720 * S, 720 * S), interpolation=cv2.INTER_LINEAR)
            hy = cv2.resize(my, (720 * S, 720 * S), interpolation=cv2.INTER_LINEAR)
            hx, hy = (hx + 0.5) * S - 0.5, (hy + 0.5) * S - 0.5
            warped = cv2.remap(prev_out, hx.astype(np.float32), hy.astype(np.float32), cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
            res = w * warped + (1 - w) * cur_sr
        cv2.imwrite(str(out / f"{nm}.png"), np.clip(res, 0, 255).astype(np.uint8))
        prev_src, prev_out, prev_i = cur_src, res, i
        n += 1
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--sr", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print("stabilised", run(Path(a.src), Path(a.sr), Path(a.out)))
