"""tools/genjutsu/temporal_smooth.py: only the hole is smoothed; outside stays byte-identical."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("temporal_smooth", ROOT / "tools" / "genjutsu" / "temporal_smooth.py")
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


def test_smoothing_touches_only_the_hole_and_halves_a_flicker_step():
    cur = np.full((8, 8, 3), 200, np.uint8)
    prev = np.full((8, 8, 3), 100, np.uint8)
    hole = np.zeros((8, 8), bool)
    hole[2:6, 2:6] = True
    out = ts.blend_step(cur, prev, hole, 0.5)
    assert (out[~hole] == cur[~hole]).all()                 # outside the hole: unchanged, byte for byte
    assert (out[hole] == 150).all()                         # inside: halfway to the motion-compensated previous frame
    assert (ts.blend_step(cur, prev, hole, 1.0)[hole] == 200).all()   # alpha 1 = no smoothing
