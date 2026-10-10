"""Per-frame human-parsing masks for a whole clip (hair / top / bottom / clothes / skin), local, soft 8-bit PNG.

Wraps command-center/bcc/direct_gen/parsing_worker.py (SegFormer-B2 clothes, ONNX, NON-COMMERCIAL licence — fine for the
owner's personal edits, not for a product). Run with FaceFusion's interpreter (onnxruntime-directml + opencv there):

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/parse_masks.py --src clip.mp4 --out masks_dir [--regions hair,top]

Writes <out>/<region>/%04d.png (1-based frame number in the source) and <out>/masks.json. Masks are smoothed over time
by an exponential moving average (alpha 0.6 toward the new frame) so recolouring does not shimmer.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center" / "bcc" / "direct_gen"))
import parsing_worker as pw  # noqa: E402

DEFAULT_MODEL = Path.home() / "Bossman" / "models" / "genjutsu" / "segformer_b2_clothes" / "model.onnx"


def ema(prev: np.ndarray | None, cur: np.ndarray, alpha: float = 0.6) -> np.ndarray:
    return cur.astype(np.float32) if prev is None else alpha * cur.astype(np.float32) + (1 - alpha) * prev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--regions", default="hair,top,bottom,clothes,skin")
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--count", type=int, default=0, help="0 = to the end")
    ap.add_argument("--provider", default="directml")
    a = ap.parse_args(argv)
    regions = [r for r in a.regions.split(",") if r]
    out = Path(a.out)
    for r in regions:
        (out / r).mkdir(parents=True, exist_ok=True)
    parser = pw.Parser(Path(a.model), a.provider)
    cap = cv2.VideoCapture(a.src)
    if a.first:
        cap.set(cv2.CAP_PROP_POS_FRAMES, a.first)
    prev: dict[str, np.ndarray | None] = {r: None for r in regions}
    n, t0 = 0, time.time()
    while True:
        if a.count and n >= a.count:
            break
        ok, frame = cap.read()
        if not ok:
            break
        masks, _info = pw.parse(frame, parser)
        idx = a.first + n + 1
        for r in regions:
            prev[r] = ema(prev[r], masks[r])
            cv2.imwrite(str(out / r / f"{idx:04d}.png"), np.clip(prev[r], 0, 255).astype(np.uint8))
        n += 1
        if n % 20 == 0:
            print(f"{n} frames {time.time() - t0:.0f}s", flush=True)
    (out / "masks.json").write_text(json.dumps({
        "src": a.src, "frames": n, "first": a.first, "regions": regions, "providers": parser.providers,
        "seconds": round(time.time() - t0, 1), "license": pw.LICENSE, "source": pw.SOURCE}, indent=1), encoding="utf-8")
    print("done", n, "frames", round(time.time() - t0), "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
