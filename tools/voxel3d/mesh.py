"""Greedy voxel meshing and a dependency-free binary glTF (.glb) writer.

Greedy meshing follows the public 0fps.net algorithm (Mikola Lysenko, 2012): for each axis and
each slice between cells, build a mask of visible faces keyed by (color, facing) and merge equal
neighbours into maximal rectangles. Output: one glTF primitive per palette color with flat
normals and an unlit-looking PBR material (metallic 0, roughness 1), 1 voxel = 1 m (one
BossBlocks block), origin at the base centre so the prop stands on the ground where placed.
Verification loads the result with trimesh and pygltflib (both MIT) as independent readers.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

from .spec import Grid

Quad = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float],
             tuple[float, float, float], tuple[int, int, int], int]  # 4 corners, normal, color


def greedy_quads(grid: Grid) -> list[Quad]:
    cells, dims = grid.cells, grid.size
    quads: list[Quad] = []
    for d in range(3):
        u, v = (d + 1) % 3, (d + 2) % 3
        du_n, dv_n = dims[u], dims[v]
        for s in range(dims[d] + 1):  # plane between cell s-1 and cell s along d
            mask: list[tuple[int, int] | None] = [None] * (du_n * dv_n)
            for j in range(dv_n):
                for i in range(du_n):
                    a = [0, 0, 0]
                    a[d], a[u], a[v] = s - 1, i, j
                    b = list(a)
                    b[d] = s
                    ka = cells.get(tuple(a)) if s > 0 else None  # type: ignore[arg-type]
                    kb = cells.get(tuple(b)) if s < dims[d] else None  # type: ignore[arg-type]
                    if ka and not kb:
                        mask[j * du_n + i] = (ka, 1)
                    elif kb and not ka:
                        mask[j * du_n + i] = (kb, -1)
            for j in range(dv_n):
                i = 0
                while i < du_n:
                    m = mask[j * du_n + i]
                    if m is None:
                        i += 1
                        continue
                    w = 1
                    while i + w < du_n and mask[j * du_n + i + w] == m:
                        w += 1
                    h = 1
                    while j + h < dv_n and all(mask[(j + h) * du_n + i + k] == m for k in range(w)):
                        h += 1
                    for hh in range(h):
                        for k in range(w):
                            mask[(j + hh) * du_n + i + k] = None
                    base = [0.0, 0.0, 0.0]
                    base[d], base[u], base[v] = s, i, j
                    du = [0.0, 0.0, 0.0]
                    du[u] = w
                    dv = [0.0, 0.0, 0.0]
                    dv[v] = h
                    p0 = tuple(base)
                    p1 = tuple(base[k] + du[k] for k in range(3))
                    p2 = tuple(base[k] + du[k] + dv[k] for k in range(3))
                    p3 = tuple(base[k] + dv[k] for k in range(3))
                    normal = [0, 0, 0]
                    normal[d] = m[1]
                    # e_u x e_v = e_d, so (p0,p1,p2,p3) is counter-clockwise seen from +d
                    corners = (p0, p1, p2, p3) if m[1] > 0 else (p0, p3, p2, p1)
                    quads.append((*corners, tuple(normal), m[0]))  # type: ignore[arg-type]
                    i += w
    return quads


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _pad(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


def encode_glb(grid: Grid, name: str = "voxel_object") -> tuple[bytes, dict]:
    quads = greedy_quads(grid)
    X, _, Z = grid.size
    off = (-X / 2.0, 0.0, -Z / 2.0)
    by_color: dict[int, list[Quad]] = {}
    for q in quads:
        by_color.setdefault(q[5], []).append(q)
    bin_parts: list[bytes] = []
    offset = 0
    buffer_views, accessors, primitives, materials = [], [], [], []
    stats = {"quads": len(quads), "vertices": 0, "triangles": 0, "primitives": 0}

    def add_view(data: bytes, target: int) -> int:
        nonlocal offset
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        padded = _pad(data)
        bin_parts.append(padded)
        offset += len(padded)
        return len(buffer_views) - 1

    for color in sorted(by_color):
        qs = by_color[color]
        pos, nrm, idx = [], [], []
        for q in qs:
            base = len(pos)
            for p in q[:4]:
                pos.append((p[0] + off[0], p[1] + off[1], p[2] + off[2]))
                nrm.append(q[4])
            idx += [base, base + 1, base + 2, base, base + 2, base + 3]
        pbytes = b"".join(struct.pack("<fff", *p) for p in pos)
        nbytes = b"".join(struct.pack("<fff", *n) for n in nrm)
        ibytes = struct.pack(f"<{len(idx)}I", *idx)
        pv, nv, iv = add_view(pbytes, 34962), add_view(nbytes, 34962), add_view(ibytes, 34963)
        mins = [min(p[k] for p in pos) for k in range(3)]
        maxs = [max(p[k] for p in pos) for k in range(3)]
        accessors += [
            {"bufferView": pv, "componentType": 5126, "count": len(pos), "type": "VEC3", "min": mins, "max": maxs},
            {"bufferView": nv, "componentType": 5126, "count": len(nrm), "type": "VEC3"},
            {"bufferView": iv, "componentType": 5125, "count": len(idx), "type": "SCALAR"},
        ]
        a = len(accessors)
        pname, hexcol = grid.palette[color - 1]
        rgb = [_srgb_to_linear(int(hexcol[k:k + 2], 16) / 255.0) for k in (1, 3, 5)]
        materials.append({"name": pname, "pbrMetallicRoughness": {"baseColorFactor": rgb + [1.0],
                                                                  "metallicFactor": 0.0, "roughnessFactor": 1.0}})
        primitives.append({"attributes": {"POSITION": a - 3, "NORMAL": a - 2}, "indices": a - 1,
                           "material": len(materials) - 1, "mode": 4})
        stats["vertices"] += len(pos)
        stats["triangles"] += len(idx) // 3
    stats["primitives"] = len(primitives)
    binary = b"".join(bin_parts)
    gltf = {
        "asset": {"version": "2.0", "generator": "Bossman voxel3d"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials, "accessors": accessors, "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(js) + 8 + len(binary)
    glb = (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
           + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)
    return glb, stats


def write_glb(grid: Grid, path: Path, name: str = "voxel_object") -> dict:
    data, stats = encode_glb(grid, name)
    Path(path).write_bytes(data)
    stats["bytes"] = len(data)
    return stats


def quad_area(q: Quad) -> float:
    p0, p1, _, p3 = q[:4]
    a = [p1[k] - p0[k] for k in range(3)]
    b = [p3[k] - p0[k] for k in range(3)]
    cx = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    return (cx[0] ** 2 + cx[1] ** 2 + cx[2] ** 2) ** 0.5
