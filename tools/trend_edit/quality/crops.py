"""4 before/after zoom crops per video (old | new, same time, same window) for the ringing / halo check.

  python crops.py --old OLD.mp4 --new NEW.mp4 --name a --out DIR
Windows (1080x1920 canvas): head+shoulders, face close, lower third (type + clothes), edge band (hair / background border).
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np

WINDOWS = {"head": (170, 250, 740, 780), "face": (290, 480, 500, 420), "type": (0, 1250, 1080, 450), "edge": (120, 150, 420, 520)}


def frame(path: str, t: float) -> np.ndarray | None:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), 1) if r.stdout else None


def duration(path: str) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True).stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--times", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = min(duration(a.old), duration(a.new))
    times = [float(x) for x in a.times.split(",")] if a.times else [d * f for f in (0.18, 0.38, 0.58, 0.78)]
    for k, (t, (wname, (x, y, w, h))) in enumerate(zip(times, WINDOWS.items()), 1):
        fo, fn = frame(a.old, t), frame(a.new, t)
        if fo is None or fn is None:
            continue
        co, cn = fo[y:y + h, x:x + w], fn[y:y + h, x:x + w]
        pad = np.full((h, 10, 3), 255, np.uint8)
        sheet = np.hstack([co, pad, cn])
        cv2.putText(sheet, f"old  t={t:.1f}s", (10, 28), 0, 0.8, (0, 255, 255), 2)
        cv2.putText(sheet, f"new  {wname}", (w + 20, 28), 0, 0.8, (0, 255, 255), 2)
        cv2.imwrite(str(out / f"zoom_{a.name}_{k}_{wname}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 93])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
