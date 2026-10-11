"""Bleed on 6 video frames of ad (b): draft (RefLook, fused alpha) vs natural + bleed control.  Metrics = hair_v2.frame_metrics
(leak = changed pixels outside the dilated person matte per changed pixel; skin = changed share of the skin region), plus
'outside_alpha_px' (changed pixels outside the fused alpha dilated 2 px).  Writes a contact sheet.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ["BUILD_ADS_FUSED"] = "1"
import cv2  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import build_ads as ba  # noqa: E402
import hair_v2 as hv  # noqa: E402
from dye_model import load_presets  # noqa: E402
from render_v3 import NaturalLook, ALIAS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", default="75,95,115,135,160,185")
    ap.add_argument("--presets", default=str(HERE / "hair_presets_natural.json"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src = ba.Source()
    presets = load_presets(a.presets)
    rows = []
    sheet = []
    for fi in [int(v) for v in a.frames.split(",")]:
        img = src.swap[fi]
        al = src.alpha(fi)
        z = np.load(ba.CACHE / f"{fi:05d}.npz")
        sk = z["skin"][:, 280:1000].astype(np.float32) / 255.0
        pe = z["person"][:, 280:1000].astype(np.float32) / 255.0
        for col in ["pink", "blue", "red", "platinum"]:
            draft = src.looks[col].apply(img, al, temporal=False)
            nl = NaturalLook(presets[ALIAS[col]], ba.CACHE)
            nl.idx = fi
            nat = nl.apply(img, al)
            tgt = (src.looks[col].stats["L_mean"], src.looks[col].stats["a_mean"], src.looks[col].stats["b_mean"])
            for tag, im in (("draft_v2b", draft), ("natural_v3", nat)):
                m = hv.frame_metrics(img, im, al, sk, pe, tgt)
                d = np.sqrt(((hv.to_lab(im) - hv.to_lab(img)) ** 2).sum(axis=2)) > 4.0
                ad = cv2.dilate((al > 0.05).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                rows.append({"frame": fi, "colour": col, "variant": tag, "leak": m["leak"], "skin": m["skin"],
                             "outside_alpha_px": int((d & ~ad).sum()), "alpha_px": int((al > 0.5).sum())})
            if col in ("pink", "platinum"):
                sheet.append(np.hstack([img, draft, nat]))
    sheet_im = np.vstack([np.hstack(sheet[i:i + 1]) for i in range(len(sheet))])
    cv2.imwrite(str(out / "video_frames_sheet.jpg"), cv2.resize(sheet_im, None, fx=0.7, fy=0.7), [cv2.IMWRITE_JPEG_QUALITY, 88])
    summ = {}
    for tag in ("draft_v2b", "natural_v3"):
        r = [x for x in rows if x["variant"] == tag]
        summ[tag] = {"leak_mean": round(float(np.mean([x["leak"] for x in r])), 4), "leak_max": round(float(np.max([x["leak"] for x in r])), 4),
                     "skin_mean": round(float(np.mean([x["skin"] for x in r])), 4),
                     "outside_alpha_px_per_alpha_px": round(float(np.sum([x["outside_alpha_px"] for x in r]) / np.sum([x["alpha_px"] for x in r])), 4)}
    (out / "video_bleed.json").write_text(json.dumps({"summary": summ, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
