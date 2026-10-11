"""Ground-truth gate for the hair look. The owner's four references are the SAME frame as the dark-hair original
(gf-refs-all/gf7.jpg) with only the hair recoloured, so  GT hair = pixels that changed between original and reference.

GT = pixels where the median over the 4 colours of dE76(reference_c, original) exceeds T, cleaned morphologically.
Gate (fixed 11.10, after the coordinator pointed out that the statistics gate is circular):
  IoU(ours, GT) >= 0.90, boundary F1 (3 px) >= 0.80, per-pixel dE00 vs reference INSIDE GT (p50 / p90 reported),
  halo (mean |dL| step across the mask edge, outside - inside, ours <= reference's own).
Run with FaceFusion's interpreter:  tools/trend_edit/gt_gate.py --out DIR [--variant name]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hair_v2 as hv  # noqa: E402
import parsing_worker as pw  # noqa: E402

VID = Path.home() / "Videos" / "Bossman-Genjutsu"
REFS = VID / "hair-refs-owner-20261011"
ORIG = VID / "gf-refs-all" / "gf7.jpg"
COLORS = ["pink", "platinum", "blue", "red"]


def load_webp(p):
    return np.array(Image.open(p).convert("RGB"))[:, :, ::-1].copy()


def gt_mask(orig_bgr, refs, T=14.0):
    lo = hv.to_lab(orig_bgr)
    des = []
    for r in refs:
        des.append(np.sqrt(((hv.to_lab(r) - lo) ** 2).sum(axis=2)))
    st = np.stack(des)
    med = np.median(st, axis=0)
    # v2 (11.10, after an eyeball check): hair in deep shadow differs little in L between the black original and a dark dye, so the
    # median over the 4 colours can leave holes inside the hair. Use "median > T  OR  max over colours > 2.2 T", then fill the
    # holes (a hair region has no holes that are not skin/background; fingers crossing it are real exceptions and are re-cut by
    # the min over colours of the reference-vs-original difference being tiny).
    m = ((cv2.GaussianBlur(med, (0, 0), 1.0) > T) | (cv2.GaussianBlur(st.max(axis=0), (0, 0), 1.0) > 2.2 * T)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    # fill enclosed holes
    inv = (1 - m).astype(np.uint8)
    n0, lab0, st0, _ = cv2.connectedComponentsWithStats(inv, 4)
    border = set(np.unique(np.concatenate([lab0[0], lab0[-1], lab0[:, 0], lab0[:, -1]])))
    for i in range(1, n0):
        if i not in border and st0[i, cv2.CC_STAT_AREA] < 0.01 * m.size:
            m[lab0 == i] = 1
    # keep components larger than 0.2 % of the image (flyaway dust removed, hanging strands kept)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] > 0.002 * m.size:
            keep[lab == i] = 1
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(keep, 8)
    if n2 > 1:
        keep = (lab2 == 1 + int(np.argmax(st2[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)    # the head only: no stray background blobs
    return keep.astype(np.float32), med


def iou(a, b):
    a, b = a > 0.5, b > 0.5
    return float((a & b).sum() / max(1, (a | b).sum()))


def boundary_f1(a, b, tol=3):
    ea = cv2.morphologyEx((a > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    eb = cv2.morphologyEx((b > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    da = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 3)
    db = cv2.distanceTransform((~eb).astype(np.uint8), cv2.DIST_L2, 3)
    p = float((db[ea] <= tol).mean()) if ea.any() else 0.0
    r = float((da[eb] <= tol).mean()) if eb.any() else 0.0
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def de00(a_bgr, b_bgr):
    la = cv2.cvtColor(a_bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
    lb = cv2.cvtColor(b_bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
    # CIEDE2000 is not in OpenCV: use the CIE94 form on 8-bit Lab (documented as dE94 in the report)
    dL = la[:, :, 0] - lb[:, :, 0]
    da = la[:, :, 1] - lb[:, :, 1]
    db = la[:, :, 2] - lb[:, :, 2]
    C1 = np.hypot(la[:, :, 1] - 128, la[:, :, 2] - 128)
    C2 = np.hypot(lb[:, :, 1] - 128, lb[:, :, 2] - 128)
    dC = C1 - C2
    dH2 = np.maximum(da * da + db * db - dC * dC, 0)
    return np.sqrt((dL / 1.0) ** 2 + (dC / (1 + 0.045 * C1)) ** 2 + dH2 / (1 + 0.015 * C1) ** 2)


def halo_step(img_bgr, mask):
    """Mean |L(outside ring) - L(inside ring)| across the matte edge: a bright/dark outline shows as a large step."""
    m = (mask > 0.5).astype(np.uint8)
    inner = cv2.erode(m, np.ones((5, 5), np.uint8)); ring_in = (m - cv2.erode(m, np.ones((3, 3), np.uint8))) > 0
    ring_out = (cv2.dilate(m, np.ones((3, 3), np.uint8)) - m) > 0
    L = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2Lab)[:, :, 0].astype(np.float32)
    return float(abs(L[ring_in].mean() - L[ring_out].mean())) if ring_in.any() and ring_out.any() else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--accept", default="", help="dir with hair_front_<colour>_ours_full.png written by accept_ref.py")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    refs = {c: load_webp(REFS / f"{c}.webp") for c in COLORS}
    h, w = refs["pink"].shape[:2]
    orig = cv2.resize(cv2.imdecode(np.fromfile(str(ORIG), np.uint8), 1), (w, h), interpolation=cv2.INTER_CUBIC)
    gt, med = gt_mask(orig, list(refs.values()))
    cv2.imwrite(str(out / "gt_mask.png"), (gt * 255).astype(np.uint8))
    ov = orig.copy()
    ov[gt > 0.5] = (0.5 * ov[gt > 0.5] + 0.5 * np.array([0, 255, 0])).astype(np.uint8)
    cv2.imwrite(str(out / "gt_overlay.jpg"), cv2.resize(ov, None, fx=0.4, fy=0.4))
    tol = max(3, int(round(0.004 * h)))
    rep = {"gt_share": float(gt.mean()), "tol_px_scaled": tol, "gate": {"IoU>=": 0.90, "F1@3px>=": 0.80}, "colours": {}}
    if a.accept:
        for c in COLORS:
            ours = cv2.imread(str(Path(a.accept) / f"hair_front_{c}_ours_full.png"))
            if ours is None:
                continue
            chg = (np.sqrt(((hv.to_lab(ours) - hv.to_lab(orig)) ** 2).sum(axis=2)) > 4.0).astype(np.float32)
            chg = cv2.morphologyEx(chg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            inside = gt > 0.5
            d94 = de00(ours, refs[c])[inside]
            base = de00(orig, refs[c])[inside]
            rep["colours"][c] = {
                "IoU": round(iou(chg, gt), 3), "F1_3px": round(boundary_f1(chg, gt, 3), 3), f"F1_{tol}px": round(boundary_f1(chg, gt, tol), 3),
                "dE94_inside_GT_p50": round(float(np.percentile(d94, 50)), 2), "dE94_inside_GT_p90": round(float(np.percentile(d94, 90)), 2),
                "dE94_orig_vs_ref_p50": round(float(np.percentile(base, 50)), 2),
                "halo_ours": round(halo_step(ours, chg), 2), "halo_reference_vs_GT": round(halo_step(refs[c], gt), 2),
                "outside_GT_changed_share": round(float(((chg > 0.5) & ~inside).sum() / max(1, (chg > 0.5).sum())), 3)}
    (out / "gt_gate.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
