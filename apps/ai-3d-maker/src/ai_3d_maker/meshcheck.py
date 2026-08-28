"""Mesh health inspection.

The whole point of this module: an STL file existing on disk says nothing about
whether it can be printed. These checks answer the questions a slicer actually
cares about — is the surface closed, is the winding consistent, are there
degenerate facets, is it one body or several, how big is it really.

Everything here is computed by this package. `cross_check_with_trimesh` is an
optional independent second opinion and is never the primary answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .mesh import Mesh, triangle_area

# Welding tolerance. STL stores float32, so exact vertex equality across
# facets is not guaranteed even for a mesh that was watertight before export.
WELD_TOLERANCE_MM = 1e-5
DEGENERATE_AREA_MM2 = 1e-9


@dataclass(slots=True)
class MeshReport:
    status: str  # PASS | WARN | FAIL
    triangles: int
    vertices_raw: int
    vertices_welded: int
    degenerate_triangles: int
    duplicate_triangles: int
    boundary_edges: int
    non_manifold_edges: int
    inconsistent_winding_edges: int
    is_watertight: bool
    is_edge_manifold: bool
    is_winding_consistent: bool
    components: int
    bbox_min_mm: tuple[float, float, float]
    bbox_max_mm: tuple[float, float, float]
    extents_mm: tuple[float, float, float]
    surface_area_mm2: float
    signed_volume_mm3: float
    units_declared: str
    self_intersection_check: str = "NOT_CHECKED"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def printable_geometry(self) -> bool:
        """True only when the surface is genuinely closed, oriented and single-bodied enough to slice."""
        return self.status != "FAIL"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["printable_geometry"] = self.printable_geometry
        return d


def _weld_index(mesh: Mesh, tol: float) -> list[int]:
    """Map each raw vertex to a welded representative index."""
    if tol <= 0:
        return list(range(len(mesh.vertices)))
    scale = 1.0 / tol
    buckets: dict[tuple[int, int, int], int] = {}
    mapping: list[int] = []
    for v in mesh.vertices:
        key = (round(v[0] * scale), round(v[1] * scale), round(v[2] * scale))
        idx = buckets.get(key)
        if idx is None:
            idx = len(buckets)
            buckets[key] = idx
        mapping.append(idx)
    return mapping


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def inspect_mesh(
    mesh: Mesh,
    *,
    weld_tolerance_mm: float = WELD_TOLERANCE_MM,
    degenerate_area_mm2: float = DEGENERATE_AREA_MM2,
) -> MeshReport:
    errors: list[str] = []
    warnings: list[str] = []

    tri_count = len(mesh.faces)
    weld = _weld_index(mesh, weld_tolerance_mm)
    welded_count = len(set(weld)) if weld else 0

    degenerate = 0
    duplicate = 0
    seen_faces: set[tuple[int, int, int]] = set()
    directed_edges: dict[tuple[int, int], int] = {}
    valid_faces: list[tuple[int, int, int]] = []

    for i, (a, b, c) in enumerate(mesh.faces):
        wa, wb, wc = weld[a], weld[b], weld[c]
        if wa == wb or wb == wc or wa == wc:
            degenerate += 1
            continue
        if triangle_area(*mesh.triangle(i)) <= degenerate_area_mm2:
            degenerate += 1
            continue
        canonical = tuple(sorted((wa, wb, wc)))
        if canonical in seen_faces:
            duplicate += 1
        else:
            seen_faces.add(canonical)
        valid_faces.append((wa, wb, wc))
        for e in ((wa, wb), (wb, wc), (wc, wa)):
            directed_edges[e] = directed_edges.get(e, 0) + 1

    undirected: dict[tuple[int, int], int] = {}
    for (u, v), n in directed_edges.items():
        key = (u, v) if u < v else (v, u)
        undirected[key] = undirected.get(key, 0) + n

    boundary_edges = sum(1 for n in undirected.values() if n == 1)
    non_manifold_edges = sum(1 for n in undirected.values() if n > 2)
    inconsistent = 0
    for (u, v), n in directed_edges.items():
        if n > 1:
            inconsistent += 1  # same directed edge used twice -> flipped neighbour
    # Each opposing pair is fine; count only same-direction repeats above.

    # Connected components over welded vertices touched by valid faces.
    if valid_faces:
        max_idx = max(max(f) for f in valid_faces) + 1
        uf = _UnionFind(max_idx)
        for a, b, c in valid_faces:
            uf.union(a, b)
            uf.union(b, c)
        roots = {uf.find(a) for f in valid_faces for a in f}
        components = len(roots)
    else:
        components = 0

    lo, hi = mesh.bounds()
    extents = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    is_edge_manifold = non_manifold_edges == 0
    is_watertight = bool(valid_faces) and boundary_edges == 0 and is_edge_manifold
    is_winding_consistent = inconsistent == 0

    if tri_count == 0:
        errors.append("mesh contains no triangles")
    if not valid_faces:
        errors.append("every triangle is degenerate; there is no surface")
    if boundary_edges:
        errors.append(f"surface is not closed: {boundary_edges} boundary edge(s)")
    if non_manifold_edges:
        errors.append(f"non-manifold geometry: {non_manifold_edges} edge(s) shared by more than two faces")
    if not is_winding_consistent:
        errors.append(f"inconsistent face winding on {inconsistent} edge(s)")
    if degenerate:
        warnings.append(f"{degenerate} degenerate/zero-area triangle(s) present")
    if duplicate:
        warnings.append(f"{duplicate} duplicate triangle(s) present")
    if components > 1:
        warnings.append(f"mesh has {components} disconnected components")
    if any(e <= 0 for e in extents):
        errors.append(f"degenerate bounding box {extents}: model is flat in at least one axis")

    # Signed volume is only meaningful for a closed surface; it is still
    # reported for an open one so the caller can see how far off it is.
    volume = mesh.volume()
    if is_watertight and volume <= 0:
        errors.append("closed mesh has non-positive volume: faces are probably inverted")

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")

    return MeshReport(
        status=status,
        triangles=tri_count,
        vertices_raw=len(mesh.vertices),
        vertices_welded=welded_count,
        degenerate_triangles=degenerate,
        duplicate_triangles=duplicate,
        boundary_edges=boundary_edges,
        non_manifold_edges=non_manifold_edges,
        inconsistent_winding_edges=inconsistent,
        is_watertight=is_watertight,
        is_edge_manifold=is_edge_manifold,
        is_winding_consistent=is_winding_consistent,
        components=components,
        bbox_min_mm=lo,
        bbox_max_mm=hi,
        extents_mm=extents,
        surface_area_mm2=mesh.area(),
        signed_volume_mm3=volume,
        units_declared=mesh.units,
        errors=errors,
        warnings=warnings,
    )


def cross_check_with_trimesh(path) -> dict:
    """Optional independent verification. Honest NOT_AVAILABLE when trimesh is absent."""
    try:
        import trimesh  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - depends on environment
        return {"status": "NOT_AVAILABLE", "reason": f"trimesh not importable: {exc}"}
    try:
        # process=True merges coincident vertices, the same welding this
        # package does before topology checks. It does not fill holes or
        # otherwise repair, so an open mesh still reads as open.
        loaded = trimesh.load_mesh(str(path), process=True)
    except Exception as exc:
        return {"status": "LOAD_FAILED", "reason": f"{type(exc).__name__}: {exc}"}
    if hasattr(loaded, "geometry"):
        geoms = list(loaded.geometry.values())
        if not geoms:
            return {"status": "LOAD_FAILED", "reason": "empty scene"}
        loaded = trimesh.util.concatenate(geoms)
    if not hasattr(loaded, "faces") or len(loaded.faces) == 0:
        return {"status": "LOAD_FAILED", "reason": "trimesh produced no triangles"}
    try:
        # trimesh delegates component splitting to scipy or networkx; neither is
        # a dependency of this app, so a missing graph engine is reported, not raised.
        components = int(len(loaded.split(only_watertight=False)))
    except Exception as exc:
        components = None
        component_note = f"component count unavailable: {type(exc).__name__}"
    else:
        component_note = None
    try:
        result = {
            "status": "OK",
            "triangles": int(len(loaded.faces)),
            "watertight": bool(loaded.is_watertight),
            "winding_consistent": bool(loaded.is_winding_consistent),
            "components": components,
            "extents_mm": [float(v) for v in loaded.extents],
            "volume_mm3": float(loaded.volume) if loaded.is_volume else None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"status": "CHECK_FAILED", "reason": f"{type(exc).__name__}: {exc}"}
    if component_note:
        result["note"] = component_note
    return result
