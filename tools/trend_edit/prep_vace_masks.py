"""Cut per-frame clothes / keep masks for a clothes_swap.py job out of a parse_masks.py run.

  python tools/trend_edit/prep_vace_masks.py --masks MASKS_DIR --first 43 --frames 77 --out JOB_MASKS

clothes_swap.py wants <out>/clothes/0001.png.. (255 = repaint) and <out>/keep/0001.png.. (255 = stays SOURCE pixels:
hair and skin, so long dark hair inside the dilated hole is not repainted as a black blob — smoke 10.10).
parse_masks.py numbers frames 1-based by absolute source frame, so source frame `first` is mask number first + 1.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--masks", type=Path, required=True)
    ap.add_argument("--first", type=int, required=True)
    ap.add_argument("--frames", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    (a.out / "clothes").mkdir(parents=True, exist_ok=True)
    (a.out / "keep").mkdir(parents=True, exist_ok=True)
    for i in range(a.frames):
        n = a.first + i + 1
        c = cv2.imread(str(a.masks / "clothes" / f"{n:04d}.png"), 0)
        h = cv2.imread(str(a.masks / "hair" / f"{n:04d}.png"), 0)
        s = cv2.imread(str(a.masks / "skin" / f"{n:04d}.png"), 0)
        if c is None or h is None or s is None:
            raise SystemExit(f"mask {n:04d} missing in {a.masks}")
        keep = np.where((h > 127) | (s > 127), 255, 0).astype(np.uint8)
        cv2.imwrite(str(a.out / "clothes" / f"{i + 1:04d}.png"), np.where(c > 127, 255, 0).astype(np.uint8))
        cv2.imwrite(str(a.out / "keep" / f"{i + 1:04d}.png"), keep)
    print("masks", a.frames, "->", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
