"""Mask-bleed before / after on the owner's frame (true GT) and on video frames (proxy).

gf7: leak = changed pixels (dE76 > 4) outside GT dilated 2 px, per GT hair area (gate <= 0.5 %), plus IoU / boundary F1 of the changed
region vs GT.  Variants: draft (RefLook, raw fused alpha) -> natural preset with raw alpha -> + bleed steps.
Video frames: no GT exists; proxy = changed pixels outside (SegFormer-hair OR fused-alpha) dilated 2 px is circular, so the proxy used
is leak into the RVM person-outside (background) and into SegFormer skin, per recoloured pixel (same definitions as hair_v2.frame_metrics).

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/eval_bleed.py --cache DIR --presets J --out DIR
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
from dye_model import apply_preset, load_presets, DyeState  # noqa: E402
from bleed import BleedParams, refine_alpha, root_map, leak_metrics  # noqa: E402
import hair_v2 as hv  # noqa: E402
import gt_gate as gg  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
LOOKS = VID / "ads-20261011" / "prep" / "accept" / "looks"
GT = VID / "ads-20261011" / "prep" / "gt" / "gt_mask.png"


def full_metrics(out, orig, gt, alpha):
    m = leak_metrics(out, orig, gt)
    d = np.sqrt(((hv.to_lab(out) - hv.to_lab(orig)) ** 2).sum(axis=2))
    chg = (d > 4.0).astype(np.float32)
    chg = cv2.morphologyEx(chg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m["F1_3px"] = round(gg.boundary_f1(chg, gt, 3), 3)
    m["F1_9px"] = round(gg.boundary_f1(chg, gt, 9), 3)
    m["alpha_iou"] = round(gg.iou(alpha, gt), 3)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--presets", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cache, outd = Path(a.cache), Path(a.out)
    outd.mkdir(parents=True, exist_ok=True)
    presets = load_presets(a.presets)
    orig = cv2.imread(str(cache / "orig.png"))
    gt = cv2.imread(str(GT), 0).astype(np.float32) / 255.0
    z = np.load(cache / "front_parse.npz")
    alpha, skin, person = z["alpha"], z["skin"], z["person"]
    rep = {"sweep_pink": {}, "colours": {}}
    p_nat = presets["pink-natural"]
    base = apply_preset(orig, alpha, p_nat, DyeState())
    rep["sweep_pink"]["natural_rawalpha"] = full_metrics(base, orig, gt, alpha)
    variants = {
        "erode0_sharp": BleedParams(erode_px=0),
        "erode1": BleedParams(erode_px=1, sharpen_hi=0.0),
        "erode2": BleedParams(erode_px=2, sharpen_hi=0.0),
        "erode2_sharp": BleedParams(erode_px=2),
        "erode3_sharp": BleedParams(erode_px=3),
        "erode2_sharp_noskin": BleedParams(erode_px=2, skin_clamp=False),
        "erode2_sharp_noperson": BleedParams(erode_px=2, person_clamp=False),
        "erode2_sharp_nobright": BleedParams(erode_px=2, bright_gate=False),
        "erode2_sharp_chroma": BleedParams(erode_px=2, target_chroma=30.0),
    }
    for n, bp in variants.items():
        al2 = refine_alpha(orig, alpha, skin, person, bp)
        rep["sweep_pink"][n] = full_metrics(apply_preset(orig, al2, p_nat, DyeState()), orig, gt, al2)
    best = BleedParams(erode_px=2)
    for c in ["pink", "platinum", "blue", "red"]:
        draft = hv.RefLook.load(LOOKS / f"{c}.npz", c).apply(orig, alpha, temporal=False)
        al2 = refine_alpha(orig, alpha, skin, person, best)
        rt = root_map(skin, al2)
        nat = apply_preset(orig, al2, presets[f"{c}-natural"], DyeState(), root=rt)
        rep["colours"][c] = {"before_draft_v2b": full_metrics(draft, orig, gt, alpha), "after_natural_bleed": full_metrics(nat, orig, gt, al2)}
        cv2.imwrite(str(outd / f"bleed_{c}_after.png"), nat)
    # crops of the worst region (around GT outline) before / after for pink
    ys, xs = np.where(gt > 0.5)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    draft = hv.RefLook.load(LOOKS / "pink.npz", "pink").apply(orig, alpha, temporal=False)
    nat = cv2.imread(str(outd / "bleed_pink_after.png"))
    # outline overlay: GT contour in green on both
    def ov(im):
        im = im.copy()
        c = cv2.morphologyEx((gt > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        im[c] = (0, 255, 0)
        return im
    h2 = (y1 - y0) // 2
    crops = [im[y0:y0 + h2, x0:x0 + 700] for im in (ov(draft), ov(nat))]
    cv2.imwrite(str(outd / "bleed_pink_contour_before_after.jpg"), np.hstack(crops), [cv2.IMWRITE_JPEG_QUALITY, 92])
    (outd / "eval_bleed.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
