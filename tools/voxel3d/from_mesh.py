"""Mesh (e.g. TripoSR image->3D output) -> voxel Grid, so a general 3D object can become a block-game asset.

    python tools/voxel3d/from_mesh.py mesh.glb --out <dir> --height 16 [--colors 8] [--name chair]

Uses trimesh (MIT) voxelization; each voxel takes the colour of the nearest mesh vertex (vertex
colours as produced by TripoSR), then colours are quantized to a small palette (Pillow median cut)
so the result fits MagicaVoxel / BossBlocks. The Y axis is up (glTF). Output goes through the same
writers and checks as prompt-made objects. Optional path: needs numpy + trimesh + Pillow.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxel3d import MAX_DIM  # noqa: E402
from voxel3d.spec import Grid, components  # noqa: E402


def voxelize(mesh_path: Path, height: int = 16, colors: int = 8, up: str = "y") -> tuple[Grid, list[str]]:
    import numpy as np
    import trimesh
    from PIL import Image

    notes: list[str] = []
    loaded = trimesh.load(str(mesh_path), force="mesh")
    mesh = loaded
    if up == "z":  # z-up source -> y-up
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    ext = mesh.extents
    pitch = float(ext[1]) / height
    if max(ext) / pitch > MAX_DIM:
        pitch = float(max(ext)) / MAX_DIM
        notes.append(f"pitch raised so the longest side fits {MAX_DIM}")
    vg = mesh.voxelized(pitch).fill()
    pts = vg.points  # voxel centres
    idx = vg.sparse_indices
    try:
        visual = mesh.visual if mesh.visual.kind == "vertex" else mesh.visual.to_color()
        vcol = np.asarray(visual.vertex_colors)[:, :3].astype(np.uint8)
        if len(vcol) != len(mesh.vertices):
            raise ValueError("colour count mismatch")
    except Exception:  # no usable colours: grey
        vcol = np.full((len(mesh.vertices), 3), 160, np.uint8)
        notes.append("mesh has no vertex colours; used grey")
    from scipy.spatial import cKDTree  # noqa: PLC0415  (scipy ships with trimesh extras)

    _, near = cKDTree(mesh.vertices).query(pts)
    rgb = vcol[near]
    # quantize to a small palette with Pillow's median cut (deterministic)
    img = Image.fromarray(rgb.reshape(-1, 1, 3), "RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=0)
    labels = np.asarray(img).reshape(-1)
    pal = img.getpalette()[: 3 * colors]
    used = sorted(set(labels.tolist()))
    remap = {old: i + 1 for i, old in enumerate(used)}
    palette = [(f"c{i + 1}", "#%02x%02x%02x" % tuple(pal[3 * old:3 * old + 3])) for i, old in enumerate(used)]
    lo = idx.min(axis=0)
    cells = {}
    for (i, j, k), lab in zip(idx - lo, labels):
        cells[(int(i), int(j), int(k))] = remap[int(lab)]
    size = tuple(int(a) + 1 for a in (idx - lo).max(axis=0))
    grid = Grid(size=size, palette=palette, cells=cells)  # type: ignore[arg-type]
    comps = components(grid.cells)
    if len(comps) > 1:
        small = [c for c in comps[1:] if len(c) <= 0.05 * len(grid.cells)]
        for comp in small:
            for c in comp:
                del grid.cells[c]
        notes.append(f"{len(comps)} parts after voxelization; dropped {len(small)} small floating piece(s)")
    ys = min(c[1] for c in grid.cells)
    if ys:
        grid.cells = {(x, y - ys, z): v for (x, y, z), v in grid.cells.items()}
    return grid, notes


def main(argv: list[str] | None = None) -> int:
    from voxel3d import bossblocks, mesh, vox
    from voxel3d.render import render_preview
    from voxel3d.spec import check_grid
    from voxel3d.verify import check_glb, check_vox

    ap = argparse.ArgumentParser()
    ap.add_argument("mesh", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--height", type=int, default=16)
    ap.add_argument("--colors", type=int, default=8)
    ap.add_argument("--up", choices=["y", "z"], default="y")
    args = ap.parse_args(argv)
    name = args.name or args.mesh.stem
    args.out.mkdir(parents=True, exist_ok=True)
    grid, notes = voxelize(args.mesh, args.height, args.colors, args.up)
    paths = {"vox": args.out / f"{name}.vox", "glb": args.out / f"{name}.glb",
             "structure": args.out / f"{name}.bbstruct.json", "preview": args.out / f"{name}.preview.png"}
    vox.write(grid, paths["vox"])
    stats = mesh.write_glb(grid, paths["glb"], name)
    bossblocks.write_structure(grid, paths["structure"], name, f"voxelized from {args.mesh.name}")
    render_preview(grid, paths["preview"])
    rep = {"source": str(args.mesh), "name": name, "size": list(grid.size), "notes": notes, "grid": check_grid(grid),
           "glb_stats": stats, "vox_check": check_vox(grid, paths["vox"]), "glb_check": check_glb(grid, paths["glb"]),
           "paths": {k: str(v) for k, v in paths.items()}}
    rep["ok"] = rep["grid"]["connected"] and rep["vox_check"]["ok"] and rep["glb_check"]["ok"]
    (args.out / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("name", "ok", "size", "notes", "glb_stats")}))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
