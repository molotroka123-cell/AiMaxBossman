"""Build hair_presets_natural.json: the 4 curves fitted to the owner's front references (stylised group) and the NATURAL family
derived with the SAME parametric model.

Natural family = shape templates measured on the owner's back-view natural-dye references (chestnut-brown / copper-red = dark class,
ash-platinum = light class; chroma rises with lightness, dark shadows, soft specular) scaled to a target (highlight lightness,
chroma ceiling, hue).  Targets below are plausible salon-result Lab values (L* of photographed hair, not a spectrophotometer).

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/build_presets.py --fit fit.json --out hair_presets_natural.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dye_model import DyePreset, lab_to_bgr, smoothstep  # noqa: E402


def swatch(p: DyePreset) -> str:
    lab = p.lab_of_t(np.array(0.8)).astype(np.float32).reshape(1, 1, 3)
    b, g, r = (int(round(v)) for v in lab_to_bgr(lab)[0, 0])
    return f"#{r:02x}{g:02x}{b:02x}"


def template(fits: dict, lhi: float) -> dict:
    """Shape parameters (as ratios) for a preset whose highlight lightness is lhi: dark class (chestnut/copper) -> light (ash)."""
    d = [fits["chestnut-brown"], fits["copper-red"]]
    l = fits["ash-platinum"]

    def ratio(f):
        return {"l0": f["L0"] / f["L1"], "gam": f["gam"], "c0": min(1.0, f["C0"] / max(1e-3, f["Cmax"])), "tc": f["tc"], "w": f["w"],
                "dh": f["dh"], "ts": f["ts"], "sd": f["sd"], "sl": f["sl"]}

    rd = {k: float(np.mean([ratio(f)[k] for f in d])) for k in ratio(d[0])}
    rl = ratio(l)
    lam = float(smoothstep(40.0, 70.0, lhi))
    return {k: (1 - lam) * rd[k] + lam * rl[k] for k in rd}


# name -> (highlight L*, chroma ceiling, hue deg, kind, label_ru, label_en, extra overrides)
TARGETS = {
    "chocolate": (28.0, 15.0, 55.0, "natural", "Шоколад", "Chocolate", {}),
    "chestnut": (34.0, 21.0, 52.0, "natural", "Каштан", "Chestnut", {}),
    "copper": (42.0, 36.0, 44.0, "natural", "Медный", "Copper", {}),
    "burgundy": (31.0, 27.0, 8.0, "natural", "Бургунди", "Burgundy", {}),
    "plum": (30.0, 24.0, 333.0, "natural", "Слива", "Plum", {}),
    "ash-blonde": (72.0, 11.0, 74.0, "natural", "Пепельный блонд", "Ash blonde", {}),
    "navy-black": (26.0, 17.0, 268.0, "natural", "Иссиня-чёрный / глубокий синий", "Deep navy / blue-black", {}),
    "teal-green": (44.0, 38.0, 190.0, "stylised", "Бирюзово-зелёный", "Teal green", {}),
}


def derive(name: str, fits: dict) -> DyePreset:
    lhi, cmax, hue, kind, ru, en, ex = TARGETS[name]
    t = template(fits, lhi)
    light = float(smoothstep(40.0, 70.0, lhi))
    p = DyePreset(
        name=name, L1=lhi, L0=max(2.0, lhi * t["l0"]), gam=t["gam"], Cmax=cmax, C0=cmax * t["c0"], tc=t["tc"], w=t["w"],
        h0=hue, dh=t["dh"] * (1.0 - 0.7 * light), ts=t["ts"], sd=max(t["sd"], 0.12), sl=t["sl"],
        rd=0.10 * (1 - light), rc=0.30 * (1 - 0.5 * light), u0=0.16 * (1 - light),
        kind=kind, label_ru=ru, label_en=en,
        source=("derived: shape template from owner back-view references (chestnut-brown, copper-red"
                + (", ash-platinum" if light > 0 else "") + f"), target highlight L*={lhi}, chroma ceiling C*={cmax}, hue {hue} deg"))
    for k, v in ex.items():
        setattr(p, k, v)
    return p


# tempered versions of the four owner colours: the fitted curves of the reference, chroma ceiling and shadow chroma pulled down,
# some under-colour showing through, soft roots.  name -> (fit, Cmax factor, C0 factor, hue shift deg, label ru, label en)
TEMPER = {
    "pink-natural": ("pink", 0.78, 0.55, 0.0, "Розовый (мягкий)", "Pink (soft)"),
    "blue-natural": ("blue", 0.80, 0.60, -3.0, "Синий (мягкий)", "Blue (soft)"),
    "red-natural": ("red", 0.72, 0.60, 3.0, "Красный (глубокий)", "Red (deep)"),
    "platinum-natural": ("platinum", 0.62, 0.50, 6.0, "Платина (пепельная)", "Platinum (ash)"),
}


def temper(name: str, fits: dict) -> DyePreset:
    base, kc, k0, dhue, ru, en = TEMPER[name]
    p = DyePreset.from_dict(fits[base])
    p.name, p.kind, p.label_ru, p.label_en = name, "natural", ru, en
    p.Cmax, p.C0, p.h0 = p.Cmax * kc, p.C0 * k0, p.h0 + dhue
    p.sd = max(p.sd, 0.25)
    p.L0 = p.L0 * 0.6 if p.L1 < 60 else p.L0        # dye hardly shows in the deepest shadow: those pixels stay near-black
    dark = 1.0 - float(smoothstep(40.0, 70.0, p.L1))
    p.rd, p.rc, p.u0 = 0.08 * dark, 0.25, 0.14 * dark
    p.source = (f"tempered fit of owner reference {base}.webp: chroma ceiling x{kc}, shadow chroma x{k0}, hue {dhue:+.0f} deg, "
                "soft roots, under-colour")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    z = json.loads(Path(a.fit).read_text(encoding="utf-8"))
    fits = {p["name"]: p for p in z["presets"]}
    out = []
    label = {"pink": ("Розовый (эталон)", "Pink (reference)"), "platinum": ("Платина (эталон)", "Platinum (reference)"),
             "blue": ("Синий (эталон)", "Blue (reference)"), "red": ("Красный (эталон)", "Red (reference)"),
             "chestnut-brown": ("Каштан (эталон, вид сзади)", "Chestnut (reference, back view)"),
             "copper-red": ("Медь (эталон, вид сзади)", "Copper (reference, back view)"),
             "ash-platinum": ("Пепельная платина (эталон, вид сзади)", "Ash platinum (reference, back view)")}
    for n, f in fits.items():
        p = DyePreset.from_dict(f)
        p.label_ru, p.label_en = label[n]
        p.kind = "stylised" if n in ("pink", "platinum", "blue", "red") else "natural"
        p.swatch = swatch(p)
        out.append(p)
    for n in TARGETS:
        p = derive(n, fits)
        p.swatch = swatch(p)
        out.append(p)
    for n in TEMPER:
        p = temper(n, fits)
        p.swatch = swatch(p)
        out.append(p)
    doc = {"version": 1, "model": "dye_model.DyePreset: L(t), C(t), h(t), specular, roots, under-colour (see dye_model.py)",
           "t": "luminance rank of the pixel among the hair pixels of its own frame (0 dark .. 1 highlight)",
           "fit_report": z.get("fit_report", {}), "presets": [p.to_dict() for p in out]}
    Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    for p in out:
        print(f"{p.name:18s} {p.kind:9s} L {p.L0:5.1f}->{p.L1:5.1f} C {p.C0:5.1f}->{p.Cmax:5.1f} h {p.h0:6.1f} {p.swatch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
