"""Watertight triangle primitives, built in pure Python.

Every primitive here is closed, edge-manifold and outward-wound by
construction, so `meshcheck.inspect_mesh` on a bare primitive must return PASS.
No numpy, no external CAD kernel: this is the zero-dependency floor of the app.
"""

from __future__ import annotations

import math

from ..mesh import Mesh, Vec3

DEFAULT_SEGMENTS = 96
MIN_SEGMENTS = 8
MAX_SEGMENTS = 512


def _clamp_segments(segments: int) -> int:
    return max(MIN_SEGMENTS, min(MAX_SEGMENTS, int(segments)))


def box(size_xyz: tuple[float, float, float], *, center: bool = False) -> Mesh:
    sx, sy, sz = (float(v) for v in size_xyz)
    if sx <= 0 or sy <= 0 or sz <= 0:
        raise ValueError("box dimensions must be positive")
    ox, oy, oz = (-sx / 2, -sy / 2, -sz / 2) if center else (0.0, 0.0, 0.0)
    verts: list[Vec3] = [
        (ox, oy, oz),
        (ox + sx, oy, oz),
        (ox + sx, oy + sy, oz),
        (ox, oy + sy, oz),
        (ox, oy, oz + sz),
        (ox + sx, oy, oz + sz),
        (ox + sx, oy + sy, oz + sz),
        (ox, oy + sy, oz + sz),
    ]
    faces = [
        # bottom (-Z), outward normal points down
        (0, 2, 1), (0, 3, 2),
        # top (+Z)
        (4, 5, 6), (4, 6, 7),
        # front (-Y)
        (0, 1, 5), (0, 5, 4),
        # right (+X)
        (1, 2, 6), (1, 6, 5),
        # back (+Y)
        (2, 3, 7), (2, 7, 6),
        # left (-X)
        (3, 0, 4), (3, 4, 7),
    ]
    return Mesh(verts, faces)


def cylinder(
    diameter: float,
    height: float,
    *,
    segments: int = DEFAULT_SEGMENTS,
    center: bool = False,
) -> Mesh:
    d = float(diameter)
    h = float(height)
    if d <= 0 or h <= 0:
        raise ValueError("cylinder diameter and height must be positive")
    n = _clamp_segments(segments)
    r = d / 2.0
    z0 = -h / 2 if center else 0.0
    z1 = z0 + h

    verts: list[Vec3] = []
    for z in (z0, z1):
        for i in range(n):
            angle = 2.0 * math.pi * i / n
            verts.append((r * math.cos(angle), r * math.sin(angle), z))
    bottom_center = len(verts)
    verts.append((0.0, 0.0, z0))
    top_center = len(verts)
    verts.append((0.0, 0.0, z1))

    faces: list[tuple[int, int, int]] = []
    for i in range(n):
        j = (i + 1) % n
        b0, b1 = i, j
        t0, t1 = n + i, n + j
        faces.append((b0, t0, t1))
        faces.append((b0, t1, b1))
        faces.append((bottom_center, b1, b0))  # bottom cap, normal -Z
        faces.append((top_center, t0, t1))     # top cap, normal +Z
    return Mesh(verts, faces)


def sphere(diameter: float, *, segments: int = DEFAULT_SEGMENTS) -> Mesh:
    d = float(diameter)
    if d <= 0:
        raise ValueError("sphere diameter must be positive")
    n = _clamp_segments(segments)
    rings = max(3, n // 2)
    r = d / 2.0

    verts: list[Vec3] = [(0.0, 0.0, r)]  # north pole
    for ring in range(1, rings):
        phi = math.pi * ring / rings
        sp, cp = math.sin(phi), math.cos(phi)
        for i in range(n):
            theta = 2.0 * math.pi * i / n
            verts.append((r * sp * math.cos(theta), r * sp * math.sin(theta), r * cp))
    south = len(verts)
    verts.append((0.0, 0.0, -r))

    def ring_index(ring: int, i: int) -> int:
        return 1 + (ring - 1) * n + (i % n)

    faces: list[tuple[int, int, int]] = []
    for i in range(n):
        faces.append((0, ring_index(1, i), ring_index(1, i + 1)))
    for ring in range(1, rings - 1):
        for i in range(n):
            a = ring_index(ring, i)
            b = ring_index(ring, i + 1)
            c = ring_index(ring + 1, i + 1)
            e = ring_index(ring + 1, i)
            faces.append((a, e, c))
            faces.append((a, c, b))
    for i in range(n):
        faces.append((south, ring_index(rings - 1, i + 1), ring_index(rings - 1, i)))
    return Mesh(verts, faces)


# ------------------------------------------------------------------ transform
def rotation_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> tuple[tuple[float, ...], ...]:
    """Extrinsic X-then-Y-then-Z rotation, matching OpenSCAD's rotate([x,y,z])."""
    rx, ry, rz = (math.radians(float(v)) for v in (rx_deg, ry_deg, rz_deg))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy, cy * sx, cy * cx),
    )


def apply_rotation(mesh: Mesh, rot_deg: tuple[float, float, float]) -> Mesh:
    if all(abs(float(v)) < 1e-12 for v in rot_deg):
        return mesh.copy()
    m = rotation_matrix(*rot_deg)
    verts = [
        (
            m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z,
        )
        for x, y, z in mesh.vertices
    ]
    return Mesh(verts, list(mesh.faces), mesh.units)
