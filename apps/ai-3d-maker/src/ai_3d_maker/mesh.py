"""Triangle mesh container and STL I/O.

Implemented without numpy/trimesh so the printability core works on a bare
Python 3.11 install. trimesh, when present, is used only as an independent
cross-check in tests and in `meshcheck.cross_check_with_trimesh`.

Export is deterministic by construction: binary STL, fixed 80-byte header with
no timestamp, float32 little-endian, triangles emitted in input order.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .errors import MeshLoadError

STL_HEADER_TAG = b"ai-3d-maker deterministic binary STL"
BINARY_TRI_SIZE = 50
MAX_REASONABLE_TRIS = 50_000_000

Vec3 = tuple[float, float, float]
Tri = tuple[int, int, int]


@dataclass(slots=True)
class Mesh:
    vertices: list[Vec3] = field(default_factory=list)
    faces: list[Tri] = field(default_factory=list)
    units: str = "mm"

    # ---------------------------------------------------------------- basics
    def __len__(self) -> int:
        return len(self.faces)

    def copy(self) -> "Mesh":
        return Mesh(list(self.vertices), list(self.faces), self.units)

    def triangle(self, index: int) -> tuple[Vec3, Vec3, Vec3]:
        a, b, c = self.faces[index]
        return self.vertices[a], self.vertices[b], self.vertices[c]

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.vertices:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def extents(self) -> Vec3:
        lo, hi = self.bounds()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    def area(self) -> float:
        return sum(triangle_area(*self.triangle(i)) for i in range(len(self.faces)))

    def volume(self) -> float:
        """Signed volume via the divergence theorem. Meaningful only if closed."""
        total = 0.0
        for i in range(len(self.faces)):
            a, b, c = self.triangle(i)
            total += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
        return total / 6.0

    # ------------------------------------------------------------ transforms
    def translated(self, offset: Vec3) -> "Mesh":
        dx, dy, dz = offset
        return Mesh([(x + dx, y + dy, z + dz) for x, y, z in self.vertices], list(self.faces), self.units)

    def scaled(self, factor: float | Vec3) -> "Mesh":
        if isinstance(factor, (int, float)):
            sx = sy = sz = float(factor)
        else:
            sx, sy, sz = (float(v) for v in factor)
        if sx == 0 or sy == 0 or sz == 0:
            raise ValueError("scale factors must be non-zero")
        verts = [(x * sx, y * sy, z * sz) for x, y, z in self.vertices]
        faces = list(self.faces)
        if sx * sy * sz < 0:  # a mirror flips orientation; restore outward winding
            faces = [(a, c, b) for a, b, c in faces]
        return Mesh(verts, faces, self.units)

    def rotated_axis_swap(self, order: tuple[int, int, int]) -> "Mesh":
        """Axis permutation used for orientation search (no shear, no scaling)."""
        i, j, k = order
        verts = [(v[i], v[j], v[k]) for v in self.vertices]
        parity = _permutation_parity(order)
        faces = list(self.faces) if parity > 0 else [(a, c, b) for a, b, c in self.faces]
        return Mesh(verts, faces, self.units)


def _permutation_parity(order: tuple[int, int, int]) -> int:
    seq = list(order)
    swaps = 0
    for i in range(len(seq)):
        for j in range(i + 1, len(seq)):
            if seq[i] > seq[j]:
                swaps += 1
    return 1 if swaps % 2 == 0 else -1


def triangle_area(a: Vec3, b: Vec3, c: Vec3) -> float:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def face_normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    mag = math.sqrt(nx * nx + ny * ny + nz * nz)
    if mag <= 0.0:
        return (0.0, 0.0, 0.0)
    return (nx / mag, ny / mag, nz / mag)


# ------------------------------------------------------------------- reading
def _finite(values) -> bool:
    return all(math.isfinite(v) for v in values)


def load_stl(path: str | Path, *, max_triangles: int = MAX_REASONABLE_TRIS) -> Mesh:
    """Parse a binary or ASCII STL. Raises MeshLoadError on anything malformed.

    This never raises struct/UnicodeDecodeError to the caller: a corrupt file
    is a refusal with a reason, not a crash.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise MeshLoadError(f"cannot read {p.name}: {exc}") from exc
    if not raw:
        raise MeshLoadError(f"{p.name} is empty")
    if _looks_ascii_stl(raw):
        return _load_ascii_stl(raw, p.name, max_triangles=max_triangles)
    return _load_binary_stl(raw, p.name, max_triangles=max_triangles)


def _looks_ascii_stl(raw: bytes) -> bool:
    head = raw[:512].lstrip()
    if not head.lower().startswith(b"solid"):
        return False
    # A binary STL may also start with "solid". Decide on the presence of the
    # ASCII keywords within the first chunk.
    probe = raw[:4096].lower()
    return b"facet" in probe and b"vertex" in probe


def _load_binary_stl(raw: bytes, name: str, *, max_triangles: int) -> Mesh:
    if len(raw) < 84:
        raise MeshLoadError(f"{name}: binary STL shorter than 84-byte minimum ({len(raw)} bytes)")
    (count,) = struct.unpack_from("<I", raw, 80)
    if count > max_triangles:
        raise MeshLoadError(f"{name}: declares {count} triangles, above the {max_triangles} limit")
    expected = 84 + count * BINARY_TRI_SIZE
    if len(raw) != expected:
        raise MeshLoadError(
            f"{name}: truncated or padded binary STL, header declares {count} triangles "
            f"({expected} bytes expected, {len(raw)} present)"
        )
    mesh = Mesh()
    index: dict[Vec3, int] = {}
    off = 84
    for i in range(count):
        try:
            # 12 floats per facet: normal (ignored, recomputed on export) + 3 vertices.
            values = struct.unpack_from("<12f", raw, off)
        except struct.error as exc:  # pragma: no cover - guarded by length check
            raise MeshLoadError(f"{name}: cannot unpack triangle {i}: {exc}") from exc
        pts = (values[3:6], values[6:9], values[9:12])
        if not _finite([c for pt in pts for c in pt]):
            raise MeshLoadError(f"{name}: triangle {i} contains NaN/Inf coordinates")
        tri = []
        for pt in pts:
            key = (float(pt[0]), float(pt[1]), float(pt[2]))
            idx = index.get(key)
            if idx is None:
                idx = len(mesh.vertices)
                index[key] = idx
                mesh.vertices.append(key)
            tri.append(idx)
        mesh.faces.append((tri[0], tri[1], tri[2]))
        off += BINARY_TRI_SIZE
    return mesh


def _load_ascii_stl(raw: bytes, name: str, *, max_triangles: int) -> Mesh:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MeshLoadError(f"{name}: ASCII STL contains non-UTF-8 bytes at offset {exc.start}") from exc
    mesh = Mesh()
    index: dict[Vec3, int] = {}
    current: list[int] = []
    facet_open = False
    for lineno, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        key = parts[0].lower()
        if key == "facet":
            if facet_open:
                raise MeshLoadError(f"{name}: nested 'facet' at line {lineno}")
            facet_open = True
            current = []
        elif key == "vertex":
            if len(parts) != 4:
                raise MeshLoadError(f"{name}: malformed vertex at line {lineno}")
            try:
                pt = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as exc:
                raise MeshLoadError(f"{name}: non-numeric vertex at line {lineno}") from exc
            if not _finite(pt):
                raise MeshLoadError(f"{name}: NaN/Inf vertex at line {lineno}")
            idx = index.get(pt)
            if idx is None:
                idx = len(mesh.vertices)
                index[pt] = idx
                mesh.vertices.append(pt)
            current.append(idx)
        elif key == "endfacet":
            if not facet_open:
                raise MeshLoadError(f"{name}: 'endfacet' without 'facet' at line {lineno}")
            if len(current) != 3:
                raise MeshLoadError(f"{name}: facet ending at line {lineno} has {len(current)} vertices, expected 3")
            mesh.faces.append((current[0], current[1], current[2]))
            if len(mesh.faces) > max_triangles:
                raise MeshLoadError(f"{name}: more than {max_triangles} triangles")
            facet_open = False
            current = []
    if facet_open:
        raise MeshLoadError(f"{name}: file ends inside an unterminated facet")
    if not mesh.faces:
        raise MeshLoadError(f"{name}: no triangles found")
    return mesh


# ------------------------------------------------------------------- writing
def _q32(value: float) -> float:
    """Round-trip a float through float32 so exports are bit-stable."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def write_stl(mesh: Mesh, path: str | Path) -> Path:
    """Write a deterministic binary STL. Identical mesh -> identical bytes."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(stl_bytes(mesh))
    return p


def stl_bytes(mesh: Mesh) -> bytes:
    header = STL_HEADER_TAG.ljust(80, b"\0")[:80]
    out = bytearray(header)
    out += struct.pack("<I", len(mesh.faces))
    for i in range(len(mesh.faces)):
        a, b, c = mesh.triangle(i)
        n = face_normal(a, b, c)
        out += struct.pack(
            "<12fH",
            _q32(n[0]), _q32(n[1]), _q32(n[2]),
            _q32(a[0]), _q32(a[1]), _q32(a[2]),
            _q32(b[0]), _q32(b[1]), _q32(b[2]),
            _q32(c[0]), _q32(c[1]), _q32(c[2]),
            0,
        )
    return bytes(out)


def write_ascii_stl(mesh: Mesh, path: str | Path, *, name: str = "ai_3d_maker") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"solid {name}"]
    for i in range(len(mesh.faces)):
        a, b, c = mesh.triangle(i)
        n = face_normal(a, b, c)
        lines.append(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}")
        lines.append("    outer loop")
        for v in (a, b, c):
            lines.append(f"      vertex {v[0]:.6e} {v[1]:.6e} {v[2]:.6e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def mesh_digest(mesh: Mesh) -> str:
    """Content hash of the exact bytes `write_stl` would produce."""
    return hashlib.sha256(stl_bytes(mesh)).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
