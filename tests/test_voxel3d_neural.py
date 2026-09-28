"""NEURAL route helpers (tools/voxel3d/neural3d.py) that run without a GPU or model weights."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("PIL")


def test_cutout_fallback_removes_plain_background(tmp_path, monkeypatch):
    from PIL import Image

    from voxel3d import neural3d

    monkeypatch.setitem(sys.modules, "rembg", None)  # force the dependency-free fallback
    img = Image.new("RGB", (64, 64), (252, 252, 252))
    for x in range(20, 44):
        for y in range(16, 48):
            img.putpixel((x, y), (200, 40, 40))
    src = tmp_path / "img.png"
    img.save(src)
    rep = neural3d.cutout(src, tmp_path / "rgba.png")
    alpha = np.asarray(Image.open(tmp_path / "rgba.png"))[:, :, 3]
    assert rep["method"] == "border flood fill" and not rep["touches_border"]
    assert alpha[32, 32] > 200 and alpha[2, 2] < 10
