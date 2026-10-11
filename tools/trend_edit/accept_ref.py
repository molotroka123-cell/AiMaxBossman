"""Acceptance test of the reference-guided hair look against the owner's 4 reference images (same frame, 4 colours).

Source = the dark-hair original of that frame (gf-refs-all/gf7.jpg). Gate (fixed 11.10 before the run):
mean Lab dE(result hair, reference hair) <= 10, L p5 / p95 within 8 of the reference, edge leak <= 1 %, skin changed <= 0.5 %.
NOTE: in-sample - the look is fitted on the same frame it is applied to; the cross-frame test is the video itself.

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/accept_ref.py --out DIR
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


def load_webp(p: Path) -> np.ndarray:
    return np.array(Image.open(p).convert("RGB"))[:, :, ::-1].copy()


def masks(parser, bgr: np.ndarray) -> dict:
    m, _ = pw.parse(bgr, parser)
    hair = m["hair"].astype(np.float32) / 255.0
    skin = m["skin"].astype(np.float32) / 255.0
    person = m["person"].astype(np.float32) / 255.0
    a = hair * (1.0 - hv.smoothstep(0.08, 0.45, cv2.GaussianBlur(skin, (0, 0), 1.2)))
    a = hv.bright_gate(a, hv.to_lab(bgr)[:, :, 0])
    return {"hair": hair, "alpha": a, "skin": skin, "person": person}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=str(ORIG))
    ap.add_argument("--refs", default="pink,platinum,blue,red", help="names; files <REFS>/<name>.webp or <REFS>/back-view/<name>.webp")
    ap.add_argument("--save", default="", help="dir: write <name>.npz RefLook files")
    ap.add_argument("--tag", default="front")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    parser = pw.Parser(hv.SEG_MODEL, "directml")
    sp = Path(a.src)
    src = load_webp(sp) if sp.suffix == ".webp" else cv2.imdecode(np.fromfile(str(sp), np.uint8), 1)
    report = {}
    for name in a.refs.split(","):
        rp = REFS / f"{name}.webp"
        rp = rp if rp.exists() else REFS / "back-view" / f"{name}.webp"
        ref = load_webp(rp)
        s = cv2.resize(src, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_CUBIC)
        mr, ms = masks(parser, ref), masks(parser, s)
        look = hv.RefLook(ref, mr["alpha"], name)
        if a.save:
            Path(a.save).mkdir(parents=True, exist_ok=True)
            look.save(Path(a.save) / f"{name}.npz")
        res = look.apply(s, ms["alpha"], temporal=False)
        lr, ls_ = hv.to_lab(ref), hv.to_lab(res)
        mref = mr["alpha"] > 0.5
        mres = ms["alpha"] > 0.5
        mean_ref, mean_res = lr[mref].mean(0), ls_[mres].mean(0)
        de = float(np.sqrt(((mean_ref - mean_res) ** 2).sum()))
        p = lambda v, q: float(np.percentile(v, q))  # noqa: E731
        d5 = abs(p(ls_[:, :, 0][mres], 5) - p(lr[:, :, 0][mref], 5))
        d95 = abs(p(ls_[:, :, 0][mres], 95) - p(lr[:, :, 0][mref], 95))
        sl = hv.to_lab(s)
        changed = np.sqrt(((sl - ls_) ** 2).sum(axis=2)) > 4.0
        pdil = cv2.dilate((ms["person"] > 0.5).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        leak = float((changed & ~pdil).sum()) / max(1, int(changed.sum()))
        sk = cv2.erode((ms["skin"] > 0.5).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        skin = float((changed & sk).sum()) / max(1, int(sk.sum()))
        ok = {"dE<=10": de <= 10, "L_p5<=8": d5 <= 8, "L_p95<=8": d95 <= 8, "leak<=1%": leak <= 0.01, "skin<=0.5%": skin <= 0.005}
        report[name] = {"dE_mean": round(de, 2), "L_p5_diff": round(d5, 2), "L_p95_diff": round(d95, 2), "leak": round(leak, 4),
                        "skin_changed": round(skin, 4), "ref_stats": look.stats, "pass": ok, "all_pass": all(ok.values())}
        # side-by-side: reference | ours (hair + face crop)
        ys, xs = np.where(mref | mres)
        y0, y1 = max(0, ys.min() - 40), min(ref.shape[0], ys.max() + 40)
        x0, x1 = max(0, xs.min() - 40), min(ref.shape[1], xs.max() + 40)
        pair = np.hstack([ref[y0:y1, x0:x1], res[y0:y1, x0:x1]])
        cv2.imwrite(str(out / f"hair_{a.tag}_{name}_reference_vs_ours.png"), pair)
        cv2.imwrite(str(out / f"hair_{a.tag}_{name}_ours_full.png"), res)
    (out / f"accept_ref_{a.tag}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: {x: v[x] for x in ("dE_mean", "L_p5_diff", "L_p95_diff", "leak", "skin_changed", "all_pass")}
                      for k, v in report.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
