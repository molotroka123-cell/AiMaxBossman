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


def test_clothes_gate_t3_v2_rule_is_relative_to_the_flat_fill_control():
    spec2 = importlib.util.spec_from_file_location("clothes_gate", ROOT / "tools" / "video_gate" / "clothes_gate.py")
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "video_gate"))
    cg = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(cg)
    assert cg.T["body_over_control_max"] == 0.02 and cg.T["body_max"] == 0.03        # thresholds as fixed in the doc
    assert cg.body_pass_v2(0.275, 0.256, 0.02) is True        # +0.019 over the control
    assert cg.body_pass_v2(0.284, 0.256, 0.02) is False       # the 10.10 v2 smoothed run: +0.028 -> FAIL, not adjusted
    assert cg.body_pass_v2(None, 0.256, 0.02) is False        # no measurement is never a pass
