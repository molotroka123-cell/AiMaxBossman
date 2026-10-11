"""Fit the dye-model parameters to the owner's reference photos (same frame, original dark hair vs dyed reference).

For every colour: t = luminance rank of the ORIGINAL pixel inside the hair mask (GT), and the reference (L, a, b) at that
pixel is binned over t (per-L-bin statistics, 40 bins, medians).  The curves of dye_model.DyePreset are fitted to the
binned medians with bounded least squares.  Front: pink / platinum / blue / red on GT masks (ads-20261011/prep/gt).
Back view: chestnut-brown / copper-red / ash-platinum with the change mask of that frame.

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/fit_presets.py --cache DIR --out fit.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dye_model import DyePreset, to_lab, ciede2000  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
GT_DIR = VID / "ads-20261011" / "prep" / "gt"
NB = 40


def rank_t(orig_bgr, mask):
    lab = to_lab(orig_bgr)
    L = lab[:, :, 0]
    m = mask > 0.5
    L = np.minimum(L, float(np.percentile(L[m], 97)) + 4.0)
    Ls = cv2.GaussianBlur(L, (0, 0), 1.8)
    samp = np.sort(Ls[m])
    t = np.searchsorted(samp, Ls, side="left") / max(1, len(samp) - 1)
    return np.clip(t, 0, 1).astype(np.float32), lab


def binned(t, ref_lab, mask):
    m = mask > 0.5
    tt = t[m]
    idx = np.minimum((tt * NB).astype(int), NB - 1)
    centers = (np.arange(NB) + 0.5) / NB
    med = np.zeros((NB, 3), np.float32)
    for k in range(NB):
        s = ref_lab[m][idx == k]
        med[k] = np.median(s, axis=0) if len(s) else np.nan
    return centers, med


def fit_one(name, centers, med, init_hue=None):
    ok = ~np.isnan(med[:, 0])
    c, y = centers[ok], med[ok]
    C = np.hypot(y[:, 1], y[:, 2])
    h = np.degrees(np.arctan2(y[:, 2], y[:, 1]))
    h0g = float(np.degrees(np.angle(np.mean(np.exp(1j * np.radians(h)) * C))))
    hu = (h - h0g + 180) % 360 - 180                      # unwrapped around the mean hue
    keys = ["L0", "L1", "gam", "C0", "Cmax", "tc", "w", "h0", "dh", "ts", "sd", "sl"]
    x0 = np.array([y[0, 0], y[-1, 0], 1.0, C[:5].mean(), C.max(), 0.4, 0.3, h0g, 0.0, 0.92, 0.1, 0.0])
    lo = np.array([0, 0, 0.3, 0, 0, 0.05, 0.05, h0g - 40, -80, 0.8, 0.0, 0.0])
    hi = np.array([100, 100, 3.0, 80, 90, 0.95, 0.6, h0g + 40, 80, 0.99, 0.8, 0.5])

    def model(x):
        p = DyePreset(name, **dict(zip(keys, x)))
        return p.lab_of_t(c)

    def res(x):
        m = model(x)
        return np.concatenate([(m[:, 0] - y[:, 0]) / 3.0, (m[:, 1] - y[:, 1]) / 2.0, (m[:, 2] - y[:, 2]) / 2.0])

    r = least_squares(res, x0, bounds=(lo, hi), loss="soft_l1", max_nfev=2000)
    p = DyePreset(name, **{k: float(v) for k, v in zip(keys, r.x)})
    m = p.lab_of_t(c)
    de = ciede2000(m, y)
    return p, {"bin_dE00_mean": float(de.mean()), "bin_dE00_max": float(de.max()), "bins": int(ok.sum()),
               "fit_cost": float(r.cost), "hue_mean_deg": h0g}


def back_mask(orig, refs):
    lo = to_lab(orig)
    st = np.stack([np.sqrt(((to_lab(r) - lo) ** 2).sum(axis=2)) for r in refs])
    med = np.median(st, axis=0)
    m = ((cv2.GaussianBlur(med, (0, 0), 1.0) > 14.0) | (cv2.GaussianBlur(st.max(axis=0), (0, 0), 1.0) > 30.0)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, stt, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        m = (lab == 1 + int(np.argmax(stt[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    return m.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cache = Path(a.cache)
    out = {"presets": [], "fit_report": {}}
    orig = cv2.imread(str(cache / "orig.png"))
    gt = cv2.imread(str(GT_DIR / "gt_mask.png"), 0).astype(np.float32) / 255.0
    t, _ = rank_t(orig, gt)
    for c in ["pink", "platinum", "blue", "red"]:
        ref = cv2.imread(str(cache / f"ref_{c}.png"))
        centers, med = binned(t, to_lab(ref), gt)
        p, rep = fit_one(c, centers, med)
        p.source = f"owner reference {c}.webp (front, gf7), GT mask, 40 L-rank bins"
        p.kind = "stylised"
        out["presets"].append(p.to_dict())
        out["fit_report"][c] = rep
    bo = cv2.imread(str(cache / "back_orig.png"))
    names = ["chestnut-brown", "copper-red", "ash-platinum"]
    brefs = [cv2.imread(str(cache / f"back_{n}.png")) for n in names]
    bm = back_mask(bo, brefs)
    cv2.imwrite(str(cache / "back_mask.png"), (bm * 255).astype(np.uint8))
    tb, _ = rank_t(bo, bm)
    for n, r in zip(names, brefs):
        centers, med = binned(tb, to_lab(r), bm)
        p, rep = fit_one(n, centers, med)
        p.source = f"owner reference back-view {n}.webp (natural-dark -> {n}), change mask, 40 L-rank bins"
        p.kind = "natural"
        out["presets"].append(p.to_dict())
        out["fit_report"]["back-" + n] = rep
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    for p in out["presets"]:
        print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items() if k not in ("source", "label_ru", "label_en", "swatch")})
    print(json.dumps(out["fit_report"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
