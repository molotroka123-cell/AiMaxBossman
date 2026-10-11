"""Keyframe picker + pack writer for the hair-stencil workflow.

  python pick_keyframes.py --src clip.mp4 --name ad-b_clip1 --span 2.47:3.93 --span 4.80:6.13 --k 9 --out-root DIR

Per sampled frame: SegFormer hair/skin (CPU) -> pose / hair-state features -> deterministic k-center (or k-means) -> k frames that cover
pose and hair-state change. For the picked frames only, the full automatic mask (SegFormer x SAM2, see auto_mask.py) is run and
a pack is written, frames at SOURCE resolution:

  DIR/<name>/frames/<name>_f00123.png          frame
  DIR/<name>/auto_masks/<name>_f00123.png      8-bit mask 0/255 (the editor's second file)
  DIR/<name>/overlays/<name>_f00123.png        frame with the mask as a semi-transparent red layer (for a quick look)
  DIR/<name>/keyframes.json                    indices, times, features, why picked
  DIR/<name>/contact_sheet.jpg

CPU only. Features are mask-geometry proxies, not a head-pose estimator (no FaceFusion, no GPU).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stencil_core as sc  # noqa: E402


def read_frames(src: Path, spans: list[tuple[float, float]] | None, step: int):
    """Yield (frame_index, time_s, bgr) for every `step`-th frame inside the spans (whole clip when spans is empty)."""
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ranges = [(int(round(a * fps)), min(n - 1, int(round(b * fps)))) for a, b in spans] if spans else [(0, n - 1)]
    out = []
    for a, b in ranges:
        cap.set(cv2.CAP_PROP_POS_FRAMES, a)
        for i in range(a, b + 1):
            ok, fr = cap.read()
            if not ok:
                break
            if (i - a) % step == 0:
                out.append((i, i / fps, fr))
    cap.release()
    return out, fps


def sample_indices(n_frames: int, n_samples: int) -> int:
    """Step so that about n_samples frames are analysed."""
    return max(1, int(round(n_frames / max(1, n_samples))))


def overlay(frame: np.ndarray, mask: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    m = (mask > 127)[..., None]
    red = np.zeros_like(frame)
    red[:, :] = (40, 40, 255)
    out = np.where(m, (frame * (1 - alpha) + red * alpha).astype(np.uint8), frame)
    edge = cv2.morphologyEx((mask > 127).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    out[edge] = (0, 255, 255)
    return out


def contact_sheet(tiles: list[np.ndarray], labels: list[str], cols: int = 3, tw: int = 420) -> np.ndarray:
    th = int(tw * tiles[0].shape[0] / tiles[0].shape[1])
    rows = []
    for r in range(0, len(tiles), cols):
        row = []
        for t, lab in zip(tiles[r:r + cols], labels[r:r + cols]):
            im = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
            cv2.putText(im, lab, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(im, lab, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            row.append(im)
        while len(row) < cols:
            row.append(np.zeros_like(row[0]))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def analyse(src: Path, spans, n_samples: int, regions_fn):
    """Per-sample records: dict(idx, t, feat, desc, hair_u8, skin_u8 small copies, frame kept only if keep_frames)."""
    # first pass only to count frames
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    total = sum(int(round((b - a) * fps)) + 1 for a, b in spans) if spans else n
    step = sample_indices(total, n_samples)
    frames, fps = read_frames(src, spans, step)
    recs = []
    for i, t, fr in frames:
        reg = regions_fn(fr)
        hair, skin = reg["hair"] > 0.5, reg["skin"] > 0.5
        recs.append({"idx": i, "t": t, "frame": fr, "hair": hair, "skin": skin,
                     "feat": sc.frame_features(hair, skin), "desc": sc.shape_descriptor(hair),
                     "sep": None})
    return recs, fps, step


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--span", action="append", default=[], help="start:end seconds, repeatable; default whole clip")
    ap.add_argument("--k", type=int, default=9)
    ap.add_argument("--samples", type=int, default=48, help="about this many frames are analysed")
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--method", default="kcenter", choices=("kcenter", "kmeans"))
    ap.add_argument("--no-sam", action="store_true", help="skip SAM2 (mask = SegFormer hair x no-skin only)")
    a = ap.parse_args(argv)
    spans = [tuple(float(x) for x in s.split(":")) for s in a.span]
    import auto_mask as am
    am_ = am.AutoMasker()
    t0 = time.time()
    recs, fps, step = analyse(Path(a.src), spans, a.samples, am_.regions)
    print(f"analysed {len(recs)} samples (step {step}) in {time.time() - t0:.0f}s", flush=True)
    feats = sc.clip_feature_matrix([r["feat"] for r in recs], [r["desc"] for r in recs])
    # weights: pose proxies (yaw, pitch) and hair-state (PCA) matter most, abs position least
    # a weak time column keeps near-static stretches from collapsing into one pick
    tcol = np.array([r["t"] for r in recs])[:, None]
    feats = np.hstack([feats, tcol])
    w = np.ones(feats.shape[1])
    w[[0, 1]] = 1.6
    w[[5, 6]] = 0.6
    w[-1] = 0.5
    picks = sc.pick_keyframes(feats, k=a.k, min_gap=2, seed=a.seed, weights=w, method=a.method)
    out = Path(a.out_root) / a.name
    for d in ("frames", "auto_masks", "overlays"):
        (out / d).mkdir(parents=True, exist_ok=True)
    tiles, labels, meta = [], [], []
    for j in picks:
        r = recs[j]
        stem = f"{a.name}_f{r['idx']:05d}"
        if a.no_sam:
            mask = (r["hair"] * 255).astype(np.uint8)
        else:
            mask = am_(r["frame"])["mask"]
        cv2.imwrite(str(out / "frames" / f"{stem}.png"), r["frame"])
        cv2.imwrite(str(out / "auto_masks" / f"{stem}.png"), mask)
        ov = overlay(r["frame"], mask)
        cv2.imwrite(str(out / "overlays" / f"{stem}.png"), ov)
        tiles.append(ov)
        labels.append(f"{stem[-6:]} t={r['t']:.2f}s")
        meta.append({"stem": stem, "frame_index": r["idx"], "time_s": round(r["t"], 3), "sample_index": j,
                     "features": dict(zip(["yaw", "pitch", "fill", "width", "height", "cx", "cy", "abs_area"],
                                          [round(float(x), 4) for x in r["feat"]])),
                     "mask_area_px": int((mask > 127).sum())})
        print("keyframe", stem, flush=True)
    cv2.imwrite(str(out / "contact_sheet.jpg"), contact_sheet(tiles, labels), [cv2.IMWRITE_JPEG_QUALITY, 88])
    h, w_ = recs[0]["frame"].shape[:2]
    (out / "keyframes.json").write_text(json.dumps({
        "source": str(a.src), "name": a.name, "spans_s": spans, "fps": fps, "size": [w_, h], "sample_step": step,
        "n_samples": len(recs), "k": a.k, "seed": a.seed, "method": a.method, "feature_note": "mask-geometry proxies, not degrees",
        "keyframes": meta}, indent=1), encoding="utf8")
    print("pack", out, "keyframes", len(meta), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
