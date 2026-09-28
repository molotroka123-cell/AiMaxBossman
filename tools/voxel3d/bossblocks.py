"""BossBlocks (Godot 4.7) export.

The game (scripts/main.gd) stores its world as `{"version": 1, "blocks": [{"x","y","z","kind"}]}`
with three block kinds (1 Grass #69aa56, 2 Sand #d8bc79, 3 Stone #858f9e) and no prefab format.
A structure file therefore reuses exactly that record schema (so the game's own loader can read
it) and adds `color` (palette index) for a future full-color block set. Cells are relative to the
base centre: x, z in [-X//2, ...], y from 0.

`install()` copies a GLB prop and a structure into a game project (assets/voxel, structures) and
is only called with an explicit --apply: writing into a project is a file mutation that goes
through the normal Bossman approval path.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .spec import Grid

GAME_KINDS = {1: ("Grass", "#69aa56"), 2: ("Sand", "#d8bc79"), 3: ("Stone", "#858f9e")}
# main.gd _load_world keeps y in [-1, 5] and |x|, |z| <= 8 (WORLD_EDGE)
WORLD_EDGE, MAX_Y, MIN_Y = 8, 5, -1


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _lab(hexcol: str) -> tuple[float, float, float]:
    """sRGB -> CIELAB (D65); plain RGB distance maps brown wood to green grass."""
    lin = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in (v / 255 for v in _rgb(hexcol))]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def nearest_kind(hexcol: str) -> int:
    lab = _lab(hexcol)
    return min(GAME_KINDS, key=lambda k: sum((a - c) ** 2 for a, c in zip(lab, _lab(GAME_KINDS[k][1]))))


def structure(grid: Grid, name: str, prompt: str = "") -> dict:
    X, Y, Z = grid.size
    ox, oz = X // 2, Z // 2
    palette = [{"index": i + 1, "name": n, "color": c, "kind": nearest_kind(c)} for i, (n, c) in enumerate(grid.palette)]
    blocks = [{"x": x - ox, "y": y, "z": z - oz, "kind": palette[k - 1]["kind"], "color": k}
              for (x, y, z), k in sorted(grid.cells.items(), key=lambda it: (it[0][1], it[0][2], it[0][0]))]
    xs = [b["x"] for b in blocks] or [0]
    zs = [b["z"] for b in blocks] or [0]
    ys = [b["y"] for b in blocks] or [0]
    fits = max(ys) <= MAX_Y and max(abs(min(xs)), abs(max(xs))) <= WORLD_EDGE and max(abs(min(zs)), abs(max(zs))) <= WORLD_EDGE
    return {
        "format": "bossblocks.structure", "version": 1, "name": name, "prompt": prompt,
        "size": [X, Y, Z], "origin": "base-center",
        "game_kinds": {str(k): v[0] for k, v in GAME_KINDS.items()},
        "fits_world_at_origin": fits,
        "palette": palette, "blocks": blocks,
    }


def write_structure(grid: Grid, path: Path, name: str, prompt: str = "") -> dict:
    data = structure(grid, name, prompt)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data


GODOT_DIR = Path(__file__).resolve().parent / "godot"


def install(game_dir: Path, name: str, glb: Path, structure_json: Path, with_scripts: bool = True) -> list[Path]:
    """Copy one generated object into a BossBlocks project. Returns the files written."""
    game_dir = Path(game_dir)
    if not (game_dir / "project.godot").is_file():
        raise FileNotFoundError(f"{game_dir} is not a Godot project (project.godot missing)")
    written = []
    for src, rel in ((glb, Path("assets/voxel") / f"{name}.glb"), (structure_json, Path("structures") / f"{name}.json")):
        dst = game_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        written.append(dst)
    if with_scripts:
        for src, rel in ((GODOT_DIR / "voxel_structures.gd", Path("scripts/voxel_structures.gd")),
                         (GODOT_DIR / "voxel_import.gd", Path("tests/voxel_import.gd")),
                         (GODOT_DIR / "voxel_capture.gd", Path("tests/voxel_capture.gd")),
                         (GODOT_DIR / "test_voxel_import.py", Path("tests/test_voxel_import.py"))):
            dst = game_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            written.append(dst)
    return written
