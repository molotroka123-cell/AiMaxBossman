"""Measure the dye model against the owner's references (same frame gf7, GT hair mask) and write acid-vs-natural crop pairs.

Per colour inside GT: per-pixel CIEDE2000 p50 / p90 vs the reference photo, chroma p95 (ours vs reference, gate: ours <= ref + 5),
for: draft = RefLook (the previous v2b method), fit = fitted dye model, natural = the derived natural variant.
  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/eval_natural.py --cache DIR --presets hair_presets_natural.json --out DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from dye_model import apply_preset, load_presets, to_lab, ciede2000, DyeState  # noqa: E402
import hair_v2 as hv  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
LOOKS = VID / "ads-20261011" / "prep" / "accept" / "looks"
GT = VID / "ads-20261011" / "prep" / "gt" / "gt_mask.png"


def stats(img, ref, gt):
    m = gt > 0.5
    li, lr = to_lab(img)[m], to_lab(ref)[m]
    de = ciede2000(li, lr)
    ci, cr = np.hypot(li[:, 1], li[:, 2]), np.hypot(lr[:, 1], lr[:, 2])
    return {"dE00_p50": round(float(np.percentile(de, 50)), 2), "dE00_p90": round(float(np.percentile(de, 90)), 2),
            "chroma_p95": round(float(np.percentile(ci, 95)), 1), "chroma_p95_ref": round(float(np.percentile(cr, 95)), 1),
            "chroma_mean": round(float(ci.mean()), 1), "chroma_mean_ref": round(float(cr.mean()), 1),
            "L_p50": round(float(np.percentile(li[:, 0], 50)), 1), "L_p50_ref": round(float(np.percentile(lr[:, 0], 50)), 1)}


def crop_box(gt, pad=20):
    ys, xs = np.where(gt > 0.5)
    return max(0, ys.min() - pad), min(gt.shape[0], ys.max() + pad), max(0, xs.min() - pad), min(gt.shape[1], xs.max() + pad)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--presets", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cache, out = Path(a.cache), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    presets = load_presets(a.presets)
    orig = cv2.imread(str(cache / "orig.png"))
    gt = cv2.imread(str(GT), 0).astype(np.float32) / 255.0
    al = cv2.GaussianBlur(gt, (0, 0), 1.0)
    y0, y1, x0, x1 = crop_box(gt)
    rep = {}
    for c in ["pink", "platinum", "blue", "red"]:
        ref = cv2.imread(str(cache / f"ref_{c}.png"))
        draft = hv.RefLook.load(LOOKS / f"{c}.npz", c).apply(orig, al, temporal=False)
        fit = apply_preset(orig, al, presets[c], DyeState())
        nat = apply_preset(orig, al, presets[f"{c}-natural"], DyeState())
        rep[c] = {"draft_RefLook": stats(draft, ref, gt), "fit": stats(fit, ref, gt), "natural": stats(nat, ref, gt)}
        tiles = [orig, draft, nat, ref]
        t = [cv2.resize(x[y0:y1, x0:x1], None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA) for x in tiles]
        for im, txt in zip(t, ["original", "acid (draft v2b)", "natural", "owner reference"]):
            cv2.putText(im, txt, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(out / f"acid_vs_natural_{c}.jpg"), np.hstack(t), [cv2.IMWRITE_JPEG_QUALITY, 90])
        cv2.imwrite(str(out / f"fit_{c}_full.png"), fit)
        cv2.imwrite(str(out / f"natural_{c}_full.png"), nat)
    # whole natural family on the same frame
    names = [n for n, p in presets.items() if p.kind in ("natural", "stylised") and n not in ("pink", "platinum", "blue", "red")]
    tiles = []
    for n in names:
        im = cv2.resize(apply_preset(orig, al, presets[n], DyeState())[y0:y1, x0:x1], None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        cv2.putText(im, n, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(im)
        rep.setdefault("family", {})[n] = stats(apply_preset(orig, al, presets[n], DyeState()), orig, gt)["chroma_p95"]
    rows = [np.hstack(tiles[i:i + 4] + [np.zeros_like(tiles[0])] * (4 - len(tiles[i:i + 4]))) for i in range(0, len(tiles), 4)]
    cv2.imwrite(str(out / "family_sheet.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
    (out / "eval_natural.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
