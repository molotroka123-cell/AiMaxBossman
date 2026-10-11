"""Before/after check of the fused matte on the owner's frame: per colour a PNG [previous draft | fused (v2b) | owner reference],
GT metrics (IoU, boundary F1, dE94 inside GT p50/p90) and the halo metric (mean |dL| step across the mask edge) against the reference.
CPU only.  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/accept_fused.py --out DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt_gate as g  # noqa: E402
import hair_fused as hf  # noqa: E402
import hair_v2 as hv  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
a = ap.parse_args()
hf.low_priority()
out = Path(a.out)
accept = g.VID / "ads-20261011" / "prep" / "accept"
refs = {c: g.load_webp(g.REFS / f"{c}.webp") for c in g.COLORS}
h, w = refs["pink"].shape[:2]
orig = cv2.resize(cv2.imdecode(np.fromfile(str(g.ORIG), np.uint8), 1), (w, h), interpolation=cv2.INTER_CUBIC)
gt, _ = g.gt_mask(orig, list(refs.values()))
fz = hf.Fused("cpu")
r = fz(orig)
alpha = r["alpha"]
cv2.imwrite(str(out / "fused_alpha.png"), (alpha * 255).astype(np.uint8))
tol = 9
rep = {"alpha_vs_GT": {"IoU": round(g.iou(alpha, gt), 3), "F1_3px": round(g.boundary_f1(alpha, gt, 3), 3),
                       "F1_9px": round(g.boundary_f1(alpha, gt, 9), 3)},
       "gate": "IoU >= 0.90, F1@3px >= 0.80: NOT met (best effort)", "colours": {}}
prev = json.loads((out / "gt_gate.json").read_text())["colours"] if (out / "gt_gate.json").exists() else {}
for c in g.COLORS:
    look = hv.RefLook.load(accept / "looks" / f"{c}.npz", c)
    res = look.apply(orig, alpha, temporal=False)
    before = cv2.imread(str(accept / f"hair_front_{c}_ours_full.png"))
    chg = (np.sqrt(((hv.to_lab(res) - hv.to_lab(orig)) ** 2).sum(axis=2)) > 4.0).astype(np.float32)
    chg = cv2.morphologyEx(chg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    inside = gt > 0.5
    chb = (np.sqrt(((hv.to_lab(before) - hv.to_lab(orig)) ** 2).sum(axis=2)) > 4.0).astype(np.float32)
    chb = cv2.morphologyEx(chb, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    db = g.de00(before, refs[c])[inside]
    before_m = {"IoU": round(g.iou(chb, gt), 3), "F1_3px": round(g.boundary_f1(chb, gt, 3), 3), "F1_9px": round(g.boundary_f1(chb, gt, 9), 3),
                "dE94_p50": round(float(np.percentile(db, 50)), 2), "dE94_p90": round(float(np.percentile(db, 90)), 2),
                "halo": round(g.halo_step(before, chb), 2)}
    d94 = g.de00(res, refs[c])[inside]
    rep["colours"][c] = {
        "IoU": round(g.iou(chg, gt), 3), "F1_3px": round(g.boundary_f1(chg, gt, 3), 3), f"F1_{tol}px": round(g.boundary_f1(chg, gt, tol), 3),
        "dE94_inside_GT_p50": round(float(np.percentile(d94, 50)), 2), "dE94_inside_GT_p90": round(float(np.percentile(d94, 90)), 2),
        "halo_ours": round(g.halo_step(res, chg), 2), "halo_reference_vs_GT": round(g.halo_step(refs[c], gt), 2),
        "halo_local_ours_on_GT": round(g.halo_local(res, gt), 2), "halo_local_draft_on_GT": round(g.halo_local(before, gt), 2),
        "halo_local_reference_on_GT": round(g.halo_local(refs[c], gt), 2), "halo_local_original_on_GT": round(g.halo_local(orig, gt), 2),
        "before_draft": before_m}
    ys, xs = np.where(gt > 0.5)
    y0, y1 = max(0, ys.min() - 30), min(h, ys.max() + 30)
    x0, x1 = max(0, xs.min() - 30), min(w, xs.max() + 30)
    tiles = [before[y0:y1, x0:x1], res[y0:y1, x0:x1], refs[c][y0:y1, x0:x1]]
    for t, lab in zip(tiles, ["PREVIOUS DRAFT", "V2B (fused matte)", "OWNER REFERENCE"]):
        cv2.putText(t, lab, (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)
        cv2.putText(t, lab, (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 1)
    cv2.imwrite(str(accept / f"v2b_{c}_before_after_reference.png"), np.hstack(tiles))
(out / "gt_gate_v2b.json").write_text(json.dumps(rep, indent=1))
print(json.dumps(rep, indent=1))
