"""Identity guard for the restored source frames: inside the head ellipse the restoration is blended 50/50 with a plain
bicubic upscale of the source. Real-ESRGAN re-draws micro features (lashes, lip lines); measured on ArcFace to her photos it
costs ~0.015 on the swapped face (0.7341 -> 0.7177 over 10 frames), the 50 % blend costs ~0.005. Outside the head the full
restoration is kept (hair edge, shoulders, background).

  python face_blend.py --src DIR_720 --sr DIR_SR_STAB(2160) --track track_clip1.json --out DIR_OUT
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

S, MIX = 3, 0.5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--sr", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tr = json.loads(Path(a.track).read_text(encoding="utf-8"))["frames"]
    ks = sorted(int(k) for k in tr)
    cx = np.array([0.5 * (tr[str(k)]["box"][0] + tr[str(k)]["box"][2]) for k in ks])
    cy = np.array([0.5 * (tr[str(k)]["box"][1] + tr[str(k)]["box"][3]) for k in ks])
    fw = np.array([tr[str(k)]["box"][2] - tr[str(k)]["box"][0] for k in ks])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    yy, xx = np.mgrid[0:720 * S, 0:720 * S].astype(np.float32)
    n = 0
    for p in sorted(Path(a.sr).glob("*.png")):
        i = int(p.stem)
        x, y, w = (float(np.interp(i, ks, v)) * S for v in (cx, cy, fw))
        d = ((xx - x) / (0.78 * w)) ** 2 + ((yy - y) / (0.95 * w)) ** 2
        m = np.clip((1.35 - d) / 0.45, 0, 1)[:, :, None] * MIX
        sr = cv2.imread(str(p)).astype(np.float32)
        src = cv2.imread(str(Path(a.src) / p.name))
        bic = cv2.resize(src, (720 * S, 720 * S), interpolation=cv2.INTER_CUBIC).astype(np.float32)
        cv2.imwrite(str(out / p.name), np.clip(sr * (1 - m) + bic * m, 0, 255).astype(np.uint8))
        n += 1
    print("blended", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
