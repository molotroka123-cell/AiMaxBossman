"""Deterministic verification of generated artifacts, using independent open-source readers.

- .vox  : py-vox-io (BSD) if installed, else the built-in minimal reader
- .glb  : pygltflib (MIT) for the container, trimesh (MIT) for geometry
- area  : greedy-meshed surface area must equal the number of exposed unit faces (exact)
- winding: every triangle's geometric normal must agree with its stored normal
The local vision model check (`vision_check`) is a sanity signal, not proof.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .spec import Grid


def check_vox(grid: Grid, path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"reader": None}
    try:
        from pyvox.parser import VoxParser  # type: ignore

        m = VoxParser(str(path)).parse()
        model = m.models[0]
        size = tuple(model.size)
        vox = {(v.x, v.y, v.z): v.c for v in model.voxels}
        out["reader"] = "py-vox-io"
    except ImportError:
        from .vox import read_basic

        d = read_basic(path)
        size = tuple(d["size"])
        vox = {(x, y, z): c for x, y, z, c in d["voxels"]}
        out["reader"] = "builtin"
    X, Y, Z = grid.size
    expected = {(x, Z - 1 - z, y): k for (x, y, z), k in grid.cells.items()}
    out.update({"size_ok": size == (X, Z, Y), "voxels": len(vox), "voxels_match": vox == expected})
    out["ok"] = out["size_ok"] and out["voxels_match"]
    return out


def check_glb(grid: Grid, path: Path) -> dict[str, Any]:
    import numpy as np
    import pygltflib  # type: ignore
    import trimesh  # type: ignore

    out: dict[str, Any] = {}
    g = pygltflib.GLTF2().load(str(path))
    out["gltf_primitives"] = sum(len(m.primitives) for m in g.meshes)
    out["gltf_materials"] = len(g.materials)
    scene = trimesh.load(str(path), force="scene")
    meshes = list(scene.geometry.values())
    verts = sum(len(m.vertices) for m in meshes)
    faces = sum(len(m.faces) for m in meshes)
    out.update({"vertices": verts, "faces": faces, "meshes": len(meshes)})
    bounds = scene.bounds
    X, Y, Z = grid.size
    bb = grid.bbox()
    exp_lo = np.array([bb[0][0] - X / 2, bb[0][1], bb[0][2] - Z / 2])
    exp_hi = np.array([bb[1][0] + 1 - X / 2, bb[1][1] + 1, bb[1][2] + 1 - Z / 2])
    out["bounds"] = [list(map(float, bounds[0])), list(map(float, bounds[1]))]
    out["bounds_ok"] = bool(np.allclose(bounds[0], exp_lo) and np.allclose(bounds[1], exp_hi))
    area = float(sum(m.area for m in meshes))
    out["area"] = area
    out["area_ok"] = abs(area - grid.exposed_faces()) < 1e-3
    # winding: geometric normal from the raw index order must match the stored NORMAL
    blob = g.binary_blob()

    def arr(acc_i: int, dtype, width: int):
        acc = g.accessors[acc_i]
        view = g.bufferViews[acc.bufferView]
        start = (view.byteOffset or 0) + (acc.byteOffset or 0)
        return np.frombuffer(blob, dtype=dtype, count=acc.count * width, offset=start).reshape(acc.count, width)

    bad = 0
    for mesh in g.meshes:
        for prim in mesh.primitives:
            pos = arr(prim.attributes.POSITION, np.float32, 3)
            nrm = arr(prim.attributes.NORMAL, np.float32, 3)
            tri = arr(prim.indices, np.uint32, 1).reshape(-1, 3)
            geo = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
            geo /= np.linalg.norm(geo, axis=1, keepdims=True)
            bad += int(((geo * nrm[tri[:, 0]]).sum(axis=1) < 0.99).sum())
    out["winding_bad_faces"] = bad
    out["sane_counts"] = 0 < faces <= 12 * len(grid.cells) and verts > 0
    out["ok"] = out["bounds_ok"] and out["area_ok"] and bad == 0 and out["sane_counts"]
    return out


LABELS_DEFAULT = ["tree", "house", "torch", "chest", "creature", "crystal", "bridge", "lamp post",
                  "car", "chair", "sword", "boat", "flower", "tower"]


def vision_check(png: Path, target: str, labels: list[str], chat) -> dict[str, Any]:
    """Two questions to the local vision model: free description + forced choice among labels."""
    img = base64.b64encode(Path(png).read_bytes()).decode()
    free = chat([{"role": "user", "content": "This picture shows one voxel-art (blocky, Minecraft-style) 3D object "
                  "from two angles. In at most 10 words, what object is it?", "images": [img]}]).strip()
    choice_prompt = ("This picture shows one voxel-art (blocky, Minecraft-style) 3D object from two angles. "
                     "Which ONE label fits best? Labels: " + ", ".join(labels)
                     + '. Reply as JSON: {"label": "<one label from the list>"}')
    raw = chat([{"role": "user", "content": choice_prompt, "images": [img]}], fmt="json")
    try:
        label = str(json.loads(raw).get("label", "")).strip().lower()
    except (json.JSONDecodeError, AttributeError):
        label = raw.strip().lower()[:40]
    return {"description": free[:200], "label": label, "target": target, "match": label == target.lower()}
