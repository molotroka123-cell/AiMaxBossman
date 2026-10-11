"""Smoke tests for the natural dye model (run: <facefusion>/.venv/Scripts/python.exe tools/trend_edit/hair_natural/test_dye_model.py)."""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dye_model import ciede2000, load_presets, apply_preset, DyeState  # noqa: E402


def test_de00_reference_pairs():
    # Sharma et al. 2005 test data, pair 1 and pair 7
    d = ciede2000(np.array([50.0, 2.6772, -79.7751]), np.array([50.0, 0.0, -82.7485]))
    assert abs(float(d) - 2.0425) < 1e-3
    d = ciede2000(np.array([50.0, 2.5, 0.0]), np.array([73.0, 25.0, -18.0]))
    assert abs(float(d) - 27.1492) < 1e-2


def test_natural_family_shape():
    P = load_presets(HERE / "hair_presets_natural.json")
    t = np.linspace(0, 1, 11)
    for n in ("chocolate", "chestnut", "copper", "burgundy", "plum", "ash-blonde", "navy-black", "pink-natural"):
        p = P[n]
        assert p.kind == "natural"
        lab = p.lab_of_t(t)
        C = np.hypot(lab[:, 1], lab[:, 2])
        assert C[0] <= C[-3] + 1e-6, n          # chroma rises with lightness (shadows less saturated)
        assert C.max() <= p.Cmax + 1e-3, n      # ceiling
        assert lab[0, 0] < lab[-1, 0], n        # shadows darker than highlights
    assert P["teal-green"].kind == "stylised"


def test_apply_keeps_background():
    P = load_presets(HERE / "hair_presets_natural.json")
    rng = np.random.default_rng(1)
    img = (rng.random((64, 64, 3)) * 40).astype(np.uint8)
    al = np.zeros((64, 64), np.float32)
    al[16:48, 16:48] = 1
    out = apply_preset(img, al, P["chestnut"], DyeState())
    assert (out[:8] == img[:8]).all() and (out[:, :8] == img[:, :8]).all()
    assert (out[24:40, 24:40] != img[24:40, 24:40]).any()


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
