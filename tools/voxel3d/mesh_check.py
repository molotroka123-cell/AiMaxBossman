"""Validator for high-poly game-prop GLBs (independent of the writer).

Reads the file with pygltflib (MIT) and checks geometry with trimesh (MIT):
- triangle budget: TRI_MIN <= triangles <= TRI_MAX (default 5k..10k)
- attributes: every primitive has POSITION, NORMAL, COLOR_0, uint indices, mode TRIANGLES
- manifold-ish: after welding by position, >= 98 % of faces sit in watertight, winding-consistent
  shells (parts are separate closed shells, as in a sculpt blockout), no edge shared by > 2 faces
  inside a shell, no degenerate triangles, no inside-out shell (negative signed volume)
- normals: unit length; outward (>= 97 % of faces agree with the stored vertex normals)
- game fit: pivot at base centre (min y = 0, x/z centred), height and footprint in range
- colour: linear COLOR_0 in [0, 1]; shown sRGB not washed out (mean saturation >= 0.18, < 5 % of
  vertices near-white)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

TRI_MIN, TRI_MAX = 5000, 10000


def _to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def read_glb(path: Path) -> list[dict[str, Any]]:
    import pygltflib  # type: ignore

    g = pygltflib.GLTF2().load(str(path))
    blob = g.binary_blob()
    types = {5126: np.float32, 5125: np.uint32, 5123: np.uint16}
    widths = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}

    def arr(i: int) -> np.ndarray:
        acc = g.accessors[i]
        view = g.bufferViews[acc.bufferView]
        start = (view.byteOffset or 0) + (acc.byteOffset or 0)
        w = widths[acc.type]
        return np.frombuffer(blob, dtype=types[acc.componentType], count=acc.count * w, offset=start).reshape(acc.count, w)

    prims = []
    for mesh in g.meshes:
        for p in mesh.primitives:
            a = p.attributes
            d: dict[str, Any] = {"mode": p.mode, "material": g.materials[p.material].name if p.material is not None else None,
                                 "emissive": (g.materials[p.material].emissiveFactor if p.material is not None else None),
                                 "has_normal": a.NORMAL is not None, "has_color": a.COLOR_0 is not None}
            d["pos"] = arr(a.POSITION).astype(float)
            d["nrm"] = arr(a.NORMAL).astype(float) if a.NORMAL is not None else None
            col = arr(a.COLOR_0).astype(float) if a.COLOR_0 is not None else np.ones((len(d["pos"]), 3))
            d["col_linear"] = col[:, :3]
            d["col_srgb"] = _to_srgb(col[:, :3])
            d["idx"] = arr(p.indices).astype(np.int64).reshape(-1, 3)
            prims.append(d)
    return prims


def check_prop_glb(path: Path, tri_min: int = TRI_MIN, tri_max: int = TRI_MAX,
                   height_range: tuple[float, float] = (0.3, 8.0), max_footprint: float = 6.0,
                   expect_height: float | None = None) -> dict[str, Any]:
    import trimesh  # type: ignore

    path = Path(path)
    prims = read_glb(path)
    out: dict[str, Any] = {"file": path.name, "bytes": path.stat().st_size, "primitives": len(prims)}
    out["attributes_ok"] = all(p["has_normal"] and p["has_color"] and p["mode"] in (None, 4) for p in prims)
    pos = np.vstack([p["pos"] for p in prims])
    nrm = np.vstack([p["nrm"] for p in prims]) if out["attributes_ok"] else np.zeros_like(pos)
    lin = np.vstack([p["col_linear"] for p in prims])
    srgb = np.vstack([p["col_srgb"] for p in prims])
    off, faces = 0, []
    for p in prims:
        faces.append(p["idx"] + off)
        off += len(p["pos"])
    F = np.vstack(faces)
    tris = len(F)
    out["triangles"] = int(tris)
    out["vertices"] = int(len(pos))
    out["budget_ok"] = tri_min <= tris <= tri_max
    # degenerate + winding vs stored normals
    tri = pos[F]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area2 = np.linalg.norm(cross, axis=1)
    out["degenerate_faces"] = int((area2 < 1e-12).sum())
    geo = cross / np.maximum(area2, 1e-30)[:, None]
    avg_n = nrm[F].mean(axis=1)
    agree = (geo * avg_n).sum(axis=1) > 0
    out["outward_fraction"] = round(float(agree.mean()), 4)
    nl = np.linalg.norm(nrm, axis=1)
    out["normals_unit"] = bool(np.all(np.abs(nl - 1) < 1e-3))
    # manifold-ish: weld by position, split into shells
    m = trimesh.Trimesh(pos, F, process=False)
    m.merge_vertices(merge_tex=True, merge_norm=True)
    shells = m.split(only_watertight=False)
    wt_faces = sum(len(s.faces) for s in shells if s.is_watertight and s.is_winding_consistent)
    out["shells"] = len(shells)
    out["watertight_shells"] = int(sum(1 for s in shells if s.is_watertight))
    out["watertight_face_fraction"] = round(min(wt_faces / max(tris, 1), 1.0), 4)
    # inside-out shells (e.g. a mirrored part whose winding was flipped twice) are invisible in
    # game under back-face culling; the stored normals follow the faces, so check signed volume
    out["inside_out_shells"] = int(sum(1 for s in shells if s.is_watertight and s.volume < 0))
    edges = np.sort(m.edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    out["nonmanifold_edges"] = int((counts > 2).sum())
    out["boundary_edges"] = int((counts == 1).sum())
    out["manifold_ok"] = (out["watertight_face_fraction"] >= 0.98 and out["degenerate_faces"] == 0
                          and out["inside_out_shells"] == 0)
    # game fit
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    ext = hi - lo
    out["bounds"] = [lo.round(4).tolist(), hi.round(4).tolist()]
    out["height_m"] = round(float(ext[1]), 4)
    out["footprint_m"] = [round(float(ext[0]), 4), round(float(ext[2]), 4)]
    centred = abs(lo[0] + hi[0]) / 2 < 1e-3 and abs(lo[2] + hi[2]) / 2 < 1e-3
    out["pivot_ok"] = bool(abs(lo[1]) < 1e-3 and centred)
    h_ok = height_range[0] <= ext[1] <= height_range[1] and max(ext[0], ext[2]) <= max_footprint
    if expect_height is not None:
        h_ok = h_ok and abs(ext[1] - expect_height) < 1e-2
    out["scale_ok"] = bool(h_ok)
    # colour
    out["colors_in_range"] = bool(lin.min() >= 0 and lin.max() <= 1.0 + 1e-6)
    mx, mn = srgb.max(axis=1), srgb.min(axis=1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0)
    out["mean_saturation"] = round(float(sat.mean()), 3)
    out["near_white_fraction"] = round(float((mn > 0.93).mean()), 4)
    out["colour_ok"] = out["colors_in_range"] and out["mean_saturation"] >= 0.18 and out["near_white_fraction"] < 0.05
    out["ok"] = bool(out["budget_ok"] and out["attributes_ok"] and out["manifold_ok"] and out["normals_unit"]
                     and out["outward_fraction"] >= 0.97 and out["pivot_ok"] and out["scale_ok"] and out["colour_ok"])
    return out
