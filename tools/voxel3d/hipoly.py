"""High-poly game props: dense parts -> quadric decimation to a triangle budget -> coloured GLB.

Pipeline (both entry points end in the same writer + `mesh_check.check_prop_glb`):
- procedural: `meshgen.Model` (dense watertight parts) -> `build_model()`
- image->3D : any coloured mesh, e.g. TripoSR output -> `from_mesh_file()`

Decimation: fast-simplification (MIT, pyvista; Sven Forstmann's Fast-Quadric-Mesh-Simplification,
MIT) over all decimatable parts at once, so the quadric error decides where the triangles go
(organic foliage keeps many, flat planks keep few). Each decimated shell is mapped back to its
part, so colours are evaluated per part on the final vertices (procedural) or transferred from the
nearest source vertices (image->3D).

Output GLB (glTF 2.0): POSITION, NORMAL, COLOR_0 (linear float RGBA, per spec), uint32 indices,
one primitive per material (matte / gloss / glow with emissive), metres, Y up, pivot at the base
centre (min y = 0, bbox centred in x/z) so it stands on the block it is placed on.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .meshgen import Model, Part, hexrgb

TRI_MIN, TRI_MAX = 5000, 10000


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _decimate(parts: list[Part], target: int) -> list[tuple[Part, np.ndarray, np.ndarray]]:
    """Returns (part, vertices, faces) for every part; decimatable parts share one global budget.

    One quadric pass over all decimatable parts together (the error metric decides where the
    triangles go). Edge collapses never join separate shells, so afterwards every connected
    component belongs to exactly one part: it is assigned by a majority vote of the nearest
    original vertex. (fast-simplification's replay_simplification returned corrupt coordinates
    on these inputs, so it is not used.) If the global pass stalls above budget, each part is
    decimated on its own with a budget proportional to its dense size."""
    import fast_simplification
    from scipy.spatial import cKDTree

    keep = [p for p in parts if not p.decimate]
    dec = [p for p in parts if p.decimate]
    kept_tris = sum(len(p.mesh.faces) for p in keep)
    out = [(p, np.asarray(p.mesh.vertices, float), np.asarray(p.mesh.faces, np.int64)) for p in keep]
    if not dec:
        return out
    verts, faces, owner = [], [], []
    base = 0
    for i, p in enumerate(dec):
        verts.append(np.asarray(p.mesh.vertices, float))
        faces.append(np.asarray(p.mesh.faces, np.int64) + base)
        owner.append(np.full(len(p.mesh.vertices), i))
        base += len(p.mesh.vertices)
    V, F, O = np.vstack(verts), np.vstack(faces), np.concatenate(owner)
    budget = max(target - kept_tris, 500)
    if len(F) <= budget:
        return out + [(p, np.asarray(p.mesh.vertices, float), np.asarray(p.mesh.faces, np.int64)) for p in dec]
    V2, F2 = fast_simplification.simplify(V, F, target_count=budget)
    if len(F2) > budget * 1.03:  # stalled: per-part budgets instead
        dense = np.array([len(p.mesh.faces) for p in dec], float)
        for p, n in zip(dec, dense):
            want = max(int(budget * n / dense.sum()), 16)
            pv, pf = fast_simplification.simplify(np.asarray(p.mesh.vertices, float), np.asarray(p.mesh.faces), target_count=want)
            out.append((p, np.asarray(pv, float), np.asarray(pf, np.int64)))
        return out
    m2 = trimesh.Trimesh(V2, F2, process=False)
    comp = trimesh.graph.connected_component_labels(m2.face_adjacency, node_count=len(F2))
    _, near = cKDTree(V).query(V2)
    vowner = O[near]
    face_owner = np.empty(len(F2), np.int64)
    for c in np.unique(comp):
        fm = comp == c
        votes = np.bincount(vowner[F2[fm]].reshape(-1), minlength=len(dec))
        face_owner[fm] = int(votes.argmax())
    for i, p in enumerate(dec):
        pf = F2[face_owner == i]
        if not len(pf):
            continue
        used = np.unique(pf)
        remap = np.full(len(V2), -1)
        remap[used] = np.arange(len(used))
        m = trimesh.Trimesh(V2[used], remap[pf], process=False)
        m.update_faces(m.nondegenerate_faces(height=1e-9))
        m.remove_unreferenced_vertices()
        if len(m.faces):
            out.append((p, np.asarray(m.vertices, float), np.asarray(m.faces, np.int64)))
    return out


def _shade(part: Part, v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """-> positions, normals, sRGB colours, faces. Flat parts get unshared vertices per face."""
    m = trimesh.Trimesh(v, f, process=False)
    if part.shading == "flat":
        pos = v[f].reshape(-1, 3)
        nrm = np.repeat(m.face_normals, 3, axis=0)
        col = np.repeat(part.paint(m.triangles_center, m.face_normals), 3, axis=0)
        return pos, nrm, col, np.arange(len(pos)).reshape(-1, 3)
    nrm = np.asarray(m.vertex_normals, float)
    return v, nrm, part.paint(v, nrm), f


def _normalize(prims: list[dict[str, Any]], height: float | None) -> dict[str, float]:
    allp = np.vstack([p["pos"] for p in prims])
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    scale = 1.0 if height is None else height / float(hi[1] - lo[1])
    shift = np.array([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])
    for p in prims:
        p["pos"] = (p["pos"] + shift) * scale
    return {"scale": scale}


def _material(key: str, glow_rgb: np.ndarray | None) -> dict[str, Any]:
    mat: dict[str, Any] = {"name": key.replace(":", "_").replace("#", ""),
                           "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0,
                                                    "roughnessFactor": 0.85}}
    if key == "gloss":
        mat["pbrMetallicRoughness"].update(metallicFactor=0.55, roughnessFactor=0.35)
    if key.startswith("glow:"):
        lin = srgb_to_linear(hexrgb(key[5:]))
        mat["emissiveFactor"] = [float(x) for x in lin]
        mat["pbrMetallicRoughness"]["roughnessFactor"] = 0.3
    return mat


def _pad(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


def encode_glb(prims: list[dict[str, Any]], name: str, extras: dict | None = None) -> bytes:
    """prims: [{material, pos (N,3), nrm (N,3), col (N,3) sRGB, idx (M,3)}] -> GLB bytes."""
    bin_parts: list[bytes] = []
    views, accessors, primitives, materials = [], [], [], []
    offset = 0

    def view(data: bytes, target: int) -> int:
        nonlocal offset
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        padded = _pad(data)
        bin_parts.append(padded)
        offset += len(padded)
        return len(views) - 1

    for p in prims:
        pos = p["pos"].astype(np.float32)
        nrm = np.nan_to_num(p["nrm"].astype(np.float32))
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = np.where(ln > 1e-6, nrm / np.maximum(ln, 1e-12), np.array([0, 1, 0], np.float32))  # degenerate -> up
        col = np.hstack([srgb_to_linear(p["col"]), np.ones((len(pos), 1))]).astype(np.float32)
        idx = p["idx"].astype(np.uint32).reshape(-1)
        a0 = len(accessors)
        accessors += [
            {"bufferView": view(pos.tobytes(), 34962), "componentType": 5126, "count": len(pos), "type": "VEC3",
             "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()},
            {"bufferView": view(nrm.tobytes(), 34962), "componentType": 5126, "count": len(nrm), "type": "VEC3"},
            {"bufferView": view(col.tobytes(), 34962), "componentType": 5126, "count": len(col), "type": "VEC4"},
            {"bufferView": view(idx.tobytes(), 34963), "componentType": 5125, "count": len(idx), "type": "SCALAR"},
        ]
        materials.append(_material(p["material"], None))
        primitives.append({"attributes": {"POSITION": a0, "NORMAL": a0 + 1, "COLOR_0": a0 + 2},
                           "indices": a0 + 3, "material": len(materials) - 1, "mode": 4})
    binary = b"".join(bin_parts)
    gltf = {
        "asset": {"version": "2.0", "generator": "Bossman voxel3d hipoly"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{"name": name, "primitives": primitives, **({"extras": extras} if extras else {})}],
        "materials": materials, "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def _group(shaded: list[tuple[str, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]) -> list[dict[str, Any]]:
    by: dict[str, dict[str, list]] = {}
    for mat, pos, nrm, col, idx in shaded:
        g = by.setdefault(mat, {"pos": [], "nrm": [], "col": [], "idx": [], "n": 0})
        g["pos"].append(pos)
        g["nrm"].append(nrm)
        g["col"].append(col)
        g["idx"].append(idx + g["n"])
        g["n"] += len(pos)
    return [{"material": k, "pos": np.vstack(g["pos"]), "nrm": np.vstack(g["nrm"]), "col": np.vstack(g["col"]),
             "idx": np.vstack(g["idx"])} for k, g in sorted(by.items())]


def build_model(model: Model, target: int | None = None) -> tuple[bytes, dict[str, Any]]:
    target = target or model.target
    dense = sum(len(p.mesh.faces) for p in model.parts)
    parts = _decimate(model.parts, target)
    shaded = []
    for part, v, f in parts:
        pos, nrm, col, idx = _shade(part, v, f)
        shaded.append((part.material, pos, nrm, col, idx))
    prims = _group(shaded)
    norm = _normalize(prims, model.height)
    tris = int(sum(len(p["idx"]) for p in prims))
    stats = {"name": model.name, "label": model.label, "method": "procedural (CSG-style parts + fBm noise)",
             "dense_triangles": int(dense), "target": int(target), "triangles": tris,
             "vertices": int(sum(len(p["pos"]) for p in prims)), "parts": len(model.parts),
             "primitives": len(prims), "height_m": model.height, "scale": norm["scale"]}
    glb = encode_glb(prims, model.name, extras={"bossman": {k: stats[k] for k in ("label", "method", "triangles")}})
    return glb, stats


def from_mesh_file(path: Path, name: str, target: int = 8000, height: float = 1.0, up: str = "y") -> tuple[bytes, dict[str, Any]]:
    """Image->3D route: decimate an existing coloured mesh (e.g. TripoSR .glb/.obj) to the budget,
    transfer vertex colours from the nearest source vertex, orient Y-up, scale to `height`."""
    import fast_simplification
    from scipy.spatial import cKDTree

    src = trimesh.load(str(path), force="mesh")
    if up == "z":
        src.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    try:
        vcol = np.asarray(src.visual.to_color().vertex_colors if src.visual.kind != "vertex" else src.visual.vertex_colors)
        vcol = vcol[:, :3].astype(float) / 255.0
        if len(vcol) != len(src.vertices):
            raise ValueError
    except Exception:
        vcol = np.full((len(src.vertices), 3), 0.63)
    V, F = np.asarray(src.vertices, float), np.asarray(src.faces, np.int64)
    # marching-cubes output often carries tiny floaters (background specks): drop shells < 2 %
    labels = trimesh.graph.connected_component_labels(src.face_adjacency, node_count=len(F))
    counts = np.bincount(labels)
    keep = counts[labels] >= 0.02 * len(F)
    dropped = int((counts < 0.02 * len(F)).sum())
    if dropped:
        F = F[keep]
    # quadric collapse occasionally pinches a marching-cubes surface into a non-manifold edge;
    # try a few nearby budgets and keep the first result whose shells are all watertight
    m = None
    tries = [target + d for d in (0, -120, 120, -240, 240, -360, 360)] if len(F) > target else [len(F)]
    for t in tries:
        V2, F2 = fast_simplification.simplify(V, F, target_count=t) if len(F) > target else (V, F)
        cand = trimesh.Trimesh(V2, F2, process=False)
        cand.update_faces(cand.nondegenerate_faces(height=1e-9))
        cand.remove_unreferenced_vertices()
        if m is None:
            m = cand
        if all(s.is_watertight for s in cand.split(only_watertight=False)):
            m = cand
            break
    _, near = cKDTree(V).query(m.vertices, k=4)
    col = vcol[near].mean(axis=1)  # average of 4 nearest source vertices: a little anti-aliasing
    prims = [{"material": "matte", "pos": np.asarray(m.vertices, float), "nrm": np.asarray(m.vertex_normals, float),
              "col": col, "idx": np.asarray(m.faces, np.int64)}]
    norm = _normalize(prims, height)
    stats = {"name": name, "label": name, "method": f"image->3D mesh ({Path(path).name}) decimated",
             "dense_triangles": int(len(F)), "target": int(target), "triangles": int(len(m.faces)),
             "vertices": int(len(m.vertices)), "parts": 1, "primitives": 1, "height_m": height, "scale": norm["scale"],
             "dropped_floaters": dropped}
    return encode_glb(prims, name, extras={"bossman": {"label": name, "method": stats["method"],
                                                        "triangles": stats["triangles"]}}), stats


def render_preview(glb_path: Path, png_path: Path, size: int = 360) -> None:
    """Two-view CPU preview (Pillow painter's algorithm, Lambert + vertex colour)."""
    from PIL import Image, ImageDraw

    from .mesh_check import read_glb

    prims = read_glb(glb_path)
    pos = np.vstack([p["pos"] for p in prims])
    col = np.vstack([p["col_srgb"] for p in prims])
    off, faces = 0, []
    for p in prims:
        faces.append(p["idx"] + off)
        off += len(p["pos"])
    F = np.vstack(faces)
    c = (pos.min(axis=0) + pos.max(axis=0)) / 2
    r = float(np.linalg.norm(pos.max(axis=0) - pos.min(axis=0))) / 2
    light = np.array([0.4, 0.8, 0.45])
    light /= np.linalg.norm(light)
    panels = []
    for yaw in (35.0, 215.0):
        a, pitch = np.radians(yaw), np.radians(22)
        Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        Rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
        R = Rx @ Ry
        v = (pos - c) @ R.T
        tri = v[F]
        wn = np.cross(pos[F][:, 1] - pos[F][:, 0], pos[F][:, 2] - pos[F][:, 0])
        wn /= np.linalg.norm(wn, axis=1, keepdims=True) + 1e-12
        vn = wn @ R.T
        vis = vn[:, 2] > 0
        shade = np.clip(wn @ light, 0, 1) * 0.65 + 0.4
        fc = np.clip(col[F].mean(axis=1) * shade[:, None], 0, 1)
        order = np.argsort(tri[:, :, 2].mean(axis=1))
        im = Image.new("RGB", (size, size), (238, 242, 247))
        dr = ImageDraw.Draw(im)
        xy = tri[:, :, :2] / r * (size * 0.46) * np.array([1, -1]) + size / 2
        for i in order:
            if vis[i]:
                dr.polygon([tuple(q) for q in xy[i]], fill=tuple(int(x * 255) for x in fc[i]))
        panels.append(im)
    out = Image.new("RGB", (size * 2, size), "white")
    for k, im in enumerate(panels):
        out.paste(im, (k * size, 0))
    out.save(png_path)
