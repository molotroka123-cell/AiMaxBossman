"""Mesh repair.

Deterministic, order-stable repairs only. Nothing here invents geometry: it
welds coincident vertices, drops facets that carry no area, removes exact
duplicates, propagates a consistent winding and fixes global inside-out
orientation. Holes are NOT filled — a mesh with boundary edges comes out of
repair still failing, which is the honest answer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .mesh import Mesh, triangle_area
from .meshcheck import DEGENERATE_AREA_MM2, WELD_TOLERANCE_MM


@dataclass(slots=True)
class RepairReport:
    welded_vertices: int = 0
    removed_degenerate: int = 0
    removed_duplicate: int = 0
    reoriented_faces: int = 0
    flipped_global: bool = False
    removed_components: int = 0
    actions: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.welded_vertices
            or self.removed_degenerate
            or self.removed_duplicate
            or self.reoriented_faces
            or self.flipped_global
            or self.removed_components
        )

    def as_dict(self) -> dict:
        return {
            "welded_vertices": self.welded_vertices,
            "removed_degenerate": self.removed_degenerate,
            "removed_duplicate": self.removed_duplicate,
            "reoriented_faces": self.reoriented_faces,
            "flipped_global": self.flipped_global,
            "removed_components": self.removed_components,
            "changed": self.changed,
            "actions": self.actions,
        }


def weld(mesh: Mesh, tolerance_mm: float = WELD_TOLERANCE_MM) -> tuple[Mesh, int]:
    if tolerance_mm <= 0:
        return mesh.copy(), 0
    scale = 1.0 / tolerance_mm
    buckets: dict[tuple[int, int, int], int] = {}
    new_verts: list[tuple[float, float, float]] = []
    mapping: list[int] = []
    for v in mesh.vertices:
        key = (round(v[0] * scale), round(v[1] * scale), round(v[2] * scale))
        idx = buckets.get(key)
        if idx is None:
            idx = len(new_verts)
            buckets[key] = idx
            new_verts.append(v)
        mapping.append(idx)
    faces = [(mapping[a], mapping[b], mapping[c]) for a, b, c in mesh.faces]
    removed = len(mesh.vertices) - len(new_verts)
    return Mesh(new_verts, faces, mesh.units), removed


def drop_degenerate(mesh: Mesh, area_epsilon_mm2: float = DEGENERATE_AREA_MM2) -> tuple[Mesh, int]:
    kept: list[tuple[int, int, int]] = []
    removed = 0
    for i, (a, b, c) in enumerate(mesh.faces):
        if a == b or b == c or a == c:
            removed += 1
            continue
        if triangle_area(*mesh.triangle(i)) <= area_epsilon_mm2:
            removed += 1
            continue
        kept.append((a, b, c))
    return Mesh(list(mesh.vertices), kept, mesh.units), removed


def drop_duplicate_faces(mesh: Mesh) -> tuple[Mesh, int]:
    seen: set[tuple[int, int, int]] = set()
    kept: list[tuple[int, int, int]] = []
    removed = 0
    for face in mesh.faces:
        key = tuple(sorted(face))
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(face)
    return Mesh(list(mesh.vertices), kept, mesh.units), removed


def prune_unused_vertices(mesh: Mesh) -> Mesh:
    used = sorted({i for face in mesh.faces for i in face})
    remap = {old: new for new, old in enumerate(used)}
    verts = [mesh.vertices[i] for i in used]
    faces = [(remap[a], remap[b], remap[c]) for a, b, c in mesh.faces]
    return Mesh(verts, faces, mesh.units)


def unify_winding(mesh: Mesh) -> tuple[Mesh, int]:
    """Propagate a consistent orientation across each connected component."""
    if not mesh.faces:
        return mesh.copy(), 0
    edge_to_faces: dict[tuple[int, int], list[int]] = {}
    for fi, (a, b, c) in enumerate(mesh.faces):
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_to_faces.setdefault(key, []).append(fi)

    faces = list(mesh.faces)
    visited = [False] * len(faces)
    flipped = 0
    for seed in range(len(faces)):
        if visited[seed]:
            continue
        visited[seed] = True
        queue = deque([seed])
        while queue:
            fi = queue.popleft()
            a, b, c = faces[fi]
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u < v else (v, u)
                for nb in edge_to_faces.get(key, ()):
                    if nb == fi or visited[nb]:
                        continue
                    na, nbb, nc = faces[nb]
                    same_direction = any(
                        (x, y) == (u, v) for x, y in ((na, nbb), (nbb, nc), (nc, na))
                    )
                    if same_direction:
                        faces[nb] = (na, nc, nbb)
                        flipped += 1
                    visited[nb] = True
                    queue.append(nb)
    return Mesh(list(mesh.vertices), faces, mesh.units), flipped


def fix_global_orientation(mesh: Mesh) -> tuple[Mesh, bool]:
    """Flip every face when the closed volume comes out negative (inside-out)."""
    if not mesh.faces:
        return mesh.copy(), False
    if mesh.volume() >= 0:
        return mesh.copy(), False
    return Mesh(list(mesh.vertices), [(a, c, b) for a, b, c in mesh.faces], mesh.units), True


def components(mesh: Mesh) -> list[list[int]]:
    """Face indices grouped by connected component (edge connectivity)."""
    edge_to_faces: dict[tuple[int, int], list[int]] = {}
    for fi, (a, b, c) in enumerate(mesh.faces):
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_to_faces.setdefault(key, []).append(fi)
    seen = [False] * len(mesh.faces)
    groups: list[list[int]] = []
    for seed in range(len(mesh.faces)):
        if seen[seed]:
            continue
        seen[seed] = True
        group = [seed]
        queue = deque([seed])
        while queue:
            fi = queue.popleft()
            a, b, c = mesh.faces[fi]
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u < v else (v, u)
                for nb in edge_to_faces.get(key, ()):
                    if not seen[nb]:
                        seen[nb] = True
                        group.append(nb)
                        queue.append(nb)
        groups.append(sorted(group))
    return groups


def keep_largest_component(mesh: Mesh) -> tuple[Mesh, int]:
    groups = components(mesh)
    if len(groups) <= 1:
        return mesh.copy(), 0
    best = max(groups, key=lambda g: sum(triangle_area(*mesh.triangle(i)) for i in g))
    faces = [mesh.faces[i] for i in best]
    return prune_unused_vertices(Mesh(list(mesh.vertices), faces, mesh.units)), len(groups) - 1


def repair_mesh(
    mesh: Mesh,
    *,
    weld_tolerance_mm: float = WELD_TOLERANCE_MM,
    drop_extra_components: bool = False,
) -> tuple[Mesh, RepairReport]:
    report = RepairReport()
    work, welded = weld(mesh, weld_tolerance_mm)
    report.welded_vertices = welded
    if welded:
        report.actions.append(f"welded {welded} coincident vertices at {weld_tolerance_mm} mm")

    work, degen = drop_degenerate(work)
    report.removed_degenerate = degen
    if degen:
        report.actions.append(f"removed {degen} degenerate triangles")

    work, dup = drop_duplicate_faces(work)
    report.removed_duplicate = dup
    if dup:
        report.actions.append(f"removed {dup} duplicate triangles")

    if drop_extra_components:
        work, dropped = keep_largest_component(work)
        report.removed_components = dropped
        if dropped:
            report.actions.append(f"dropped {dropped} smaller disconnected component(s)")

    work, flipped = unify_winding(work)
    report.reoriented_faces = flipped
    if flipped:
        report.actions.append(f"reoriented {flipped} faces for consistent winding")

    work, global_flip = fix_global_orientation(work)
    report.flipped_global = global_flip
    if global_flip:
        report.actions.append("flipped whole mesh: closed volume was negative")

    work = prune_unused_vertices(work)
    return work, report
