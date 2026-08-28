"""Boolean (CSG) backend dispatch.

Union / difference / intersection of triangle meshes. The only backend that is
actually wired here is `manifold3d`, which is a real, robust CSG kernel. When
it is not installed the app says so — it does not fake a boolean by gluing
triangle soups together, because that produces a file that looks like an STL
and slices into garbage.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import CapabilityUnavailableError
from ..mesh import Mesh


@dataclass(frozen=True, slots=True)
class BackendInfo:
    name: str
    available: bool
    version: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "available": self.available, "version": self.version, "reason": self.reason}


def manifold3d_info() -> BackendInfo:
    try:
        import manifold3d  # noqa: PLC0415
    except Exception as exc:
        return BackendInfo("manifold3d", False, reason=f"import failed: {exc}")
    version = getattr(manifold3d, "__version__", None)
    if version is None:
        try:
            from importlib.metadata import version as _v  # noqa: PLC0415

            version = _v("manifold3d")
        except Exception:  # pragma: no cover
            version = "unknown"
    return BackendInfo("manifold3d", True, version=str(version))


def available_backend() -> BackendInfo:
    info = manifold3d_info()
    if info.available:
        return info
    return BackendInfo("none", False, reason=info.reason)


def _to_manifold(mesh: Mesh):
    import numpy as np  # noqa: PLC0415
    import manifold3d as m3  # noqa: PLC0415

    if not mesh.faces:
        raise ValueError("cannot convert an empty mesh to a manifold")
    verts = np.asarray(mesh.vertices, dtype=np.float32).reshape(-1, 3)
    tris = np.asarray(mesh.faces, dtype=np.uint32).reshape(-1, 3)
    return m3.Manifold(m3.Mesh(vert_properties=verts, tri_verts=tris))


def _from_manifold(man) -> Mesh:
    raw = man.to_mesh()
    verts = [(float(a), float(b), float(c)) for a, b, c in raw.vert_properties[:, :3]]
    faces = [(int(a), int(b), int(c)) for a, b, c in raw.tri_verts]
    return Mesh(verts, faces)


def _require_backend() -> None:
    info = available_backend()
    if not info.available:
        raise CapabilityUnavailableError(
            "no CSG backend available; install manifold3d to combine or subtract solids",
            detail={"backend": info.as_dict()},
        )


def _op(a: Mesh, b: Mesh, kind: str) -> Mesh:
    _require_backend()
    ma, mb = _to_manifold(a), _to_manifold(b)
    if kind == "union":
        res = ma + mb
    elif kind == "difference":
        res = ma - mb
    elif kind == "intersection":
        res = ma ^ mb
    else:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown boolean {kind}")
    if res.is_empty():
        raise ValueError(f"{kind} produced an empty solid")
    return _from_manifold(res)


def union(a: Mesh, b: Mesh) -> Mesh:
    return _op(a, b, "union")


def difference(a: Mesh, b: Mesh) -> Mesh:
    return _op(a, b, "difference")


def intersection(a: Mesh, b: Mesh) -> Mesh:
    return _op(a, b, "intersection")


def is_available() -> bool:
    return available_backend().available
