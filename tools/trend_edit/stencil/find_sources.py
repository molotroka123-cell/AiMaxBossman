"""Source finder: which clips are easiest for hair recolouring (hair boundary NOT lying on dark clothes / dark background)?

  python find_sources.py --out DIR [--samples 10] [--clips a.mp4 b.mp4 ...]

Per clip: `samples` frames spread over the whole clip; per frame SegFormer hair/skin (CPU, no SAM: the cheap mask is enough to
see what the boundary touches), then stencil_core.separability():
  dark_clothes_bg = share of hair-boundary pixels with a dark (L* < 30) NON-hair, non-skin pixel within 12 px
  neck_shadow     = same for dark pixels hugging the skin class (shadow under the chin / on the neck)
  score = 1 - (dark_clothes_bg + 0.5 * neck_shadow)       (higher = easier)
A clip is eligible only when hair is found in >= 60 % of the sampled frames, head at least 70 px tall and NOT cut by the picture edge; clips are ranked by
the median score. Writes ranking.json, ranking.csv and contact_<clip>.jpg (hair overlay + score per sampled frame).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stencil_core as sc  # noqa: E402
import pick_keyframes as pk  # noqa: E402

HOME = Path.home()
TEST = HOME / "Bossman" / "video-testset-private"
GEN = HOME / "Videos" / "Bossman-Genjutsu"
DEFAULT_CLIPS = [
    TEST / "ref-clip-01.mp4", TEST / "ref-clip-02.mov", TEST / "ref-clip-03-pult.mp4", TEST / "ref-clip-04.mp4",
    TEST / "ref-clip-05.mp4", TEST / "ref-clip-06.mp4", TEST / "ref-promo-07.mp4",     # 08 / 09 are byte-identical to 07
    GEN / "source-1.mp4", GEN / "source-2.mp4", GEN / "run-20261010b" / "src10.mp4", GEN / "run-20261010d" / "src.mp4",
    GEN / "v2-20261010" / "final-v2-clip1.mp4", GEN / "v2-20261010" / "final-v2-clip2.mp4",
    GEN / "v2-20261010" / "final-v2-kisliy.mp4", GEN / "promo-queue-20261010" / "04b-trend-b-hair-original-4x3.mp4",
]


def sample_clip(path: Path, n: int):
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out = []
    for i in np.linspace(int(0.03 * total), int(0.97 * total) - 1, n).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if ok:
            out.append((int(i), int(i) / fps, fr))
    cap.release()
    return out, total, fps


def score_clip(path: Path, regions_fn, n: int, sheet_dir: Path | None) -> dict:
    frames, total, fps = sample_clip(path, n)
    rows, tiles, labels = [], [], []
    for i, t, fr in frames:
        reg = regions_fn(fr)
        skin_s = cv2.GaussianBlur(reg["skin"], (0, 0), 1.2)
        hair = (reg["hair"] > 0.5) & (skin_s < 0.3)
        H, W = hair.shape
        ys, xs = np.where(hair)
        head_h = int(ys.max() - ys.min()) if len(ys) else 0
        L = cv2.cvtColor(fr.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)[:, :, 0]
        sep = sc.separability(hair, reg["skin"] > 0.5, L)
        # hair cut by the picture edge (round crops, head out of frame) cannot be recoloured as a whole: not usable
        import parsing_worker as pw_
        t_, b_, l_, r_ = pw_.picture_box(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
        clipped = bool(len(ys) and (ys.min() <= t_ + 3 or xs.min() <= l_ + 3 or xs.max() >= r_ - 4 or ys.max() >= b_ - 4))
        has_hair = len(ys) > 0.002 * H * W and head_h >= 70 and not clipped
        rows.append({"frame": i, "t": round(t, 2), "has_hair": bool(has_hair), "clipped": clipped, "head_h_px": head_h,
                     "hair_L_median": float(np.median(L[hair])) if len(ys) else None, **{k: round(float(v), 4) for k, v in sep.items()}})
        if sheet_dir is not None:
            ov = pk.overlay(fr, (hair * 255).astype(np.uint8))
            tiles.append(ov)
            labels.append(f"f{i} s={sep['score']:.2f} d={sep['dark_clothes_bg']:.2f} n={sep['neck_shadow']:.2f}")
    ok = [r for r in rows if r["has_hair"]]
    res = {"clip": str(path), "name": path.name, "size": [int(frames[0][2].shape[1]), int(frames[0][2].shape[0])] if frames else None,
           "frames": total, "fps": fps, "sampled": len(rows), "with_hair": len(ok),
           "eligible": bool(rows) and len(ok) >= 0.6 * len(rows), "frames_detail": rows}
    if ok:
        res.update({"score": float(np.median([r["score"] for r in ok])),
                    "dark_clothes_bg": float(np.median([r["dark_clothes_bg"] for r in ok])),
                    "neck_shadow": float(np.median([r["neck_shadow"] for r in ok])),
                    "head_h_px": int(np.median([r["head_h_px"] for r in ok])),
                    "hair_L": float(np.median([r["hair_L_median"] for r in ok if r["hair_L_median"] is not None]))})
    else:
        res.update({"score": 0.0, "dark_clothes_bg": None, "neck_shadow": None, "head_h_px": 0, "hair_L": None})
    if sheet_dir is not None and tiles:
        sheet_dir.mkdir(parents=True, exist_ok=True)
        pick = list(range(0, len(tiles), max(1, len(tiles) // 6)))[:6]
        sheet = pk.contact_sheet([tiles[j] for j in pick], [labels[j] for j in pick], cols=3, tw=380)
        sp = sheet_dir / f"contact_{path.stem}.jpg"
        cv2.imwrite(str(sp), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        res["contact_sheet"] = str(sp)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--clips", nargs="*")
    a = ap.parse_args(argv)
    import auto_mask as am
    parser = am.AutoMasker.__new__(am.AutoMasker)          # SegFormer only: no SAM sessions needed here
    import parsing_worker as pw
    parser.pw = pw
    parser.parser = pw.Parser(am.SEG_MODEL, "cpu")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    clips = [Path(c) for c in a.clips] if a.clips else DEFAULT_CLIPS
    res = []
    t0 = time.time()
    for c in clips:
        if not c.exists():
            print("missing", c, flush=True)
            continue
        r = score_clip(c, parser.regions, a.samples, out)
        res.append(r)
        print(f"{c.name:42s} score {r['score']:.3f} dark {r['dark_clothes_bg']} neck {r['neck_shadow']} head {r['head_h_px']}px "
              f"hair {r['with_hair']}/{r['sampled']} ({time.time() - t0:.0f}s)", flush=True)
    res.sort(key=lambda r: (not r["eligible"], -r["score"]))
    (out / "ranking.json").write_text(json.dumps(res, indent=1), encoding="utf8")
    with open(out / "ranking.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "clip", "eligible", "score", "dark_clothes_bg", "neck_shadow", "head_h_px", "hair_L", "with_hair", "sampled"])
        for i, r in enumerate(res, 1):
            w.writerow([i, r["name"], r["eligible"], round(r["score"], 3), r["dark_clothes_bg"] and round(r["dark_clothes_bg"], 3),
                        r["neck_shadow"] and round(r["neck_shadow"], 3), r["head_h_px"], r["hair_L"] and round(r["hair_L"], 1),
                        r["with_hair"], r["sampled"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
