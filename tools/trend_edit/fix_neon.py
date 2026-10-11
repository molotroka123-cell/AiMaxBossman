"""Background-swap composite that repairs RVM cut-outs and makes the person sit in the new light, no GPU.

Two jobs on top of a tools/genjutsu/background_swap.py job (src/, alpha/, final/ PNG folders):

1. REPAIR: RVM's alpha drops on fast hands and motion-blurred limbs, so pieces of the OLD room stay inside the new
   background. SegFormer (parse_masks.py: hair, skin, clothes) knows where the person is; where it is surer than RVM the
   source pixel is kept (never removes anything RVM kept).
2. MATCH (external review 10.10: "the character does not match the neon light"):
   - colour temperature: the person's Lab a/b mean moves 12 % toward the plate's;
   - reflected colour: the plate, heavily blurred, is lerped into the person by 9 % (left edge picks up the left neon,
     right edge the right one) — never over the face area's identity features at that strength;
   - rim light: a thin band inside the silhouette gets the plate's local colour, screened on;
   - contact shadow: the plate is darkened below / behind the silhouette (soft, offset down);
   - sharpness / grain: the plate is softened (sigma 1.4) and given luma grain so a sharp SDXL plate does not sit on top
     of a soft, 2x-upscaled person.

  python tools/trend_edit/fix_neon.py --job JOB_DIR --masks MASKS_DIR --bg plate.png --out matched.mp4 [--no-match]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def prepare_plate(plate: np.ndarray, soften: float = 1.4) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(plate for compositing, wide-blurred plate for light colour, mean Lab of the plate)."""
    soft = cv2.GaussianBlur(plate, (0, 0), soften) if soften > 0 else plate
    wide = cv2.GaussianBlur(plate, (0, 0), max(20.0, plate.shape[1] / 24.0))
    lab = cv2.cvtColor(plate.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab).reshape(-1, 3).mean(axis=0)
    return soft, wide, lab


def clean_foreground(src: np.ndarray, alpha: np.ndarray, final: np.ndarray, plate: np.ndarray) -> np.ndarray:
    """Un-premultiply RVM's composite: F = (final - (1 - a) * plate) / a  (a > 0.05), the source elsewhere."""
    a = alpha[:, :, None]
    f = (final.astype(np.float32) - (1 - a) * plate.astype(np.float32)) / np.maximum(a, 0.05)
    return np.where(a > 0.05, np.clip(f, 0, 255), src.astype(np.float32))


def fix_frame(src: np.ndarray, alpha_u8: np.ndarray, final: np.ndarray, seg_u8: np.ndarray, plate: np.ndarray,
              wide: np.ndarray | None = None, plate_lab: np.ndarray | None = None, match: bool = True, rng=None,
              dilate: int = 7, feather: int = 5) -> tuple[np.ndarray, float]:
    """One frame, BGR uint8 everywhere. Returns (composite, share of pixels where SegFormer added person)."""
    h, w = src.shape[:2]
    a = alpha_u8.astype(np.float32) / 255.0
    s = seg_u8.astype(np.float32) / 255.0
    # only confident SegFormer pixels, and only near what RVM already kept: SegFormer's low-resolution arm masks are
    # boxy and, far from the body, repainted pieces of the old room (frame 70 test, 10.10)
    s = np.where(s > 0.6, s, 0.0)
    near = cv2.dilate((a > 0.5).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * max(8, h // 45) + 1,) * 2))
    s = s * near
    if dilate > 0:
        s = cv2.dilate(s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate)))
    if feather > 0:
        s = cv2.GaussianBlur(s, (0, 0), feather / 2.0)
    share = float(((s - a) > 0.1).mean())
    a2 = np.maximum(a, s)
    fg = clean_foreground(src, a, final, plate)
    fg = np.where((s > a)[:, :, None], src.astype(np.float32), fg)
    bg = plate.astype(np.float32)
    if match and wide is not None and plate_lab is not None:
        # colour temperature
        lab = cv2.cvtColor(np.clip(fg, 0, 255).astype(np.uint8).astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)
        inside = a2 > 0.9
        if inside.sum() > 500:
            mean_ab = lab[inside][:, 1:].mean(axis=0)
            lab[:, :, 1] += (plate_lab[1] - mean_ab[0]) * 0.12
            lab[:, :, 2] += (plate_lab[2] - mean_ab[1]) * 0.12
        fg = np.clip(cv2.cvtColor(lab, cv2.COLOR_Lab2BGR), 0, 1) * 255.0
        # reflected colour from the local neon
        fg = fg * 0.91 + wide.astype(np.float32) * 0.09
        # rim light
        inner = cv2.GaussianBlur(a2, (0, 0), max(3.0, h / 220.0))
        band = np.clip(a2 - inner, 0.0, 1.0)
        band = band / max(1e-3, float(band.max())) if band.max() > 0 else band
        rim = np.clip(wide.astype(np.float32) * 1.7 + 25.0, 0, 255)
        fg = 255.0 - (255.0 - fg) * (255.0 - rim * (0.55 * band)[:, :, None]) / 255.0
        # contact shadow on the plate
        sh = np.roll(a2, int(0.03 * h), axis=0)
        sh = cv2.GaussianBlur(sh, (0, 0), 0.025 * h)
        bg = bg * (1.0 - 0.38 * sh * (1.0 - a2))[:, :, None]
        # grain (the plate is clean, the footage is not)
        if rng is not None:
            bg = bg + rng.normal(0.0, 2.2, (h, w, 1)).astype(np.float32)
    out = a2[:, :, None] * fg + (1.0 - a2[:, :, None]) * bg
    return np.clip(out, 0, 255).astype(np.uint8), share


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--masks", type=Path, required=True)
    ap.add_argument("--bg", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fps", type=float, default=24)
    ap.add_argument("--no-match", action="store_true", help="repair only, no light matching")
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int, default=0)
    a = ap.parse_args(argv)
    n = len(list((a.job / "src").glob("*.png")))
    last = a.last or n
    first = cv2.imread(str(a.job / "src" / "0001.png"))
    h, w = first.shape[:2]
    plate = cv2.imread(str(a.bg))
    if plate.shape[:2] != (h, w):
        plate = cv2.resize(plate, (w, h), interpolation=cv2.INTER_AREA)
    soft, wide, plate_lab = prepare_plate(plate, 0.0 if a.no_match else 1.4)
    rng = np.random.default_rng(7)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(a.fps),
                          "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-pix_fmt", "yuv420p", str(a.out)],
                         stdin=subprocess.PIPE)
    added = []
    for i in range(a.first, last + 1):
        src = cv2.imread(str(a.job / "src" / f"{i:04d}.png"))
        al = cv2.imread(str(a.job / "alpha" / f"{i:04d}.png"), 0)
        fin = cv2.imread(str(a.job / "final" / f"{i:04d}.png"))
        segs = [cv2.imread(str(a.masks / r / f"{i:04d}.png"), 0) for r in ("hair", "skin", "clothes")]
        seg = np.maximum.reduce([m for m in segs if m is not None])
        out, share = fix_frame(src, al, fin, seg, soft, wide, plate_lab, match=not a.no_match, rng=rng)
        added.append(share)
        p.stdin.write(out.tobytes())
    p.stdin.close()
    p.wait()
    print(f"frames {last - a.first + 1}, mean share SegFormer added {np.mean(added):.4f}, match={not a.no_match}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
