"""One-off cache for the natural-hair work: front frame gf7 (matte candidates, skin, person) and back-view change masks.

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/prep_cache.py --out DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import hair_fused as hf  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
REFS = VID / "hair-refs-owner-20261011"
GT = VID / "ads-20261011" / "prep" / "gt"


def webp(p):
    return np.array(Image.open(p).convert("RGB"))[:, :, ::-1].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    hf.low_priority()
    refs = {c: webp(REFS / f"{c}.webp") for c in ["pink", "platinum", "blue", "red"]}
    h, w = refs["pink"].shape[:2]
    orig = cv2.resize(cv2.imdecode(np.fromfile(str(VID / "gf-refs-all" / "gf7.jpg"), np.uint8), 1), (w, h), interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(out / "orig.png"), orig)
    for c, r in refs.items():
        cv2.imwrite(str(out / f"ref_{c}.png"), r)
    f = hf.Fused("cpu")
    r = f(orig)
    np.savez_compressed(out / "front_parse.npz", alpha=r["alpha"], hair=r["hair"], skin=r["skin"], person=r["person"])
    print("front parsed", r["alpha"].shape, float(r["alpha"].mean()))
    # back view
    bk = REFS / "back-view"
    nat = webp(bk / "natural-dark.webp")
    cv2.imwrite(str(out / "back_orig.png"), nat)
    for c in ["chestnut-brown", "copper-red", "ash-platinum"]:
        cv2.imwrite(str(out / f"back_{c}.png"), webp(bk / f"{c}.webp"))
    rb = f(nat)
    np.savez_compressed(out / "back_parse.npz", alpha=rb["alpha"], hair=rb["hair"], skin=rb["skin"], person=rb["person"])
    print("back", nat.shape, [webp(bk / f"{c}.webp").shape for c in ["chestnut-brown", "copper-red", "ash-platinum"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
