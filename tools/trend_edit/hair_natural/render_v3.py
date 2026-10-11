"""Re-render the hair ads with the NATURAL dye presets + bleed control (v3), reusing the existing face-swapped masters.

Same builders as build_ads.py (hair_cycle = ad b, studio_promo = promo 6, clean_ba = clean before/after); only the hair look changes:
src.looks[colour] = NaturalLook (dye model + refine_alpha + root map) instead of the RefLook / hv.recolor draft.
CPU only, below-normal priority, no FaceFusion.

  <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/render_v3.py hair_cycle --out ad-b_v3.mp4 [--work DIR]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["BUILD_ADS_FUSED"] = "1"          # fused matte (hair_fused.py), same as v2b

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import build_ads as ba  # noqa: E402
from dye_model import DyeState, apply_preset, load_presets  # noqa: E402
from bleed import BleedParams, refine_alpha, root_map  # noqa: E402

PRESETS_JSON = HERE / "hair_presets_natural.json"
#: colour names used by the ad specs -> natural preset
ALIAS = {"pink": "pink-natural", "blue": "blue-natural", "red": "red-natural", "platinum": "platinum-natural",
         "copper": "copper", "burgundy": "burgundy", "chocolate": "chocolate", "chestnut": "chestnut", "plum": "plum",
         "teal": "teal-green", "green": "teal-green", "blonde": "ash-blonde", "purple": "plum", "black": "navy-black"}


class NaturalLook:
    """Drop-in for hair_v2.RefLook: apply(img720, alpha720) -> img.  `idx` (set by the patched compose) selects the cached
    skin / person parse of that source frame for the bleed clamps and the root map."""

    def __init__(self, preset, cache_dir: Path):
        self.p = preset
        self.cache = cache_dir
        self.state = DyeState()
        self.idx: int | None = None
        self.bp = BleedParams(erode_px=2, target_chroma=float(preset.Cmax))
        m = preset.lab_of_t(np.array(0.5))
        self.stats = {"L_mean": float(m[0]), "a_mean": float(m[1]), "b_mean": float(m[2])}   # build_ads frame_metrics target

    def apply(self, img: np.ndarray, alpha: np.ndarray, **_kw) -> np.ndarray:
        skin = person = None
        if self.idx is not None:
            f = self.cache / f"{self.idx:05d}.npz"
            if f.exists():
                z = np.load(f)
                skin = z["skin"][:, 280:1000].astype(np.float32) / 255.0
                person = z["person"][:, 280:1000].astype(np.float32) / 255.0
        al = refine_alpha(img, alpha, skin, person, self.bp)
        root = root_map(skin, al) if skin is not None else None
        return apply_preset(img, al, self.p, self.state, root=root)


def install() -> None:
    presets = load_presets(PRESETS_JSON)
    init0 = ba.Source.__init__

    def init(self):
        init0(self)
        self.looks = {name: NaturalLook(presets[pn], ba.CACHE) for name, pn in ALIAS.items() if pn in presets}

    ba.Source.__init__ = init
    compose0 = ba.compose

    def compose(src, ex, st, t_abs, al_hair, idx, img_swap, img_orig):
        for lk in src.looks.values():
            lk.idx = idx
        return compose0(src, ex, st, t_abs, al_hair, idx, img_swap, img_orig)

    ba.compose = compose


if __name__ == "__main__":
    install()
    sys.exit(ba.main())
