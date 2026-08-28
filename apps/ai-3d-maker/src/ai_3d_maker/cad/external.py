"""External CAD engines: OpenSCAD and CadQuery.

Neither is required. Both are probed honestly: if the binary or module is not
present, the caller gets status NOT_AVAILABLE with the reason, never a
fabricated success.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..spec import DesignSpec
from .compiler import compile_scad


def openscad_info(binary: str = "openscad") -> dict:
    path = shutil.which(binary)
    return {
        "name": "openscad",
        "available": path is not None,
        "path": path,
        "reason": None if path else f"{binary!r} not found on PATH",
    }


def cadquery_info() -> dict:
    try:
        import cadquery  # noqa: PLC0415
    except Exception as exc:
        return {"name": "cadquery", "available": False, "path": None, "reason": f"import failed: {exc}"}
    return {
        "name": "cadquery",
        "available": True,
        "path": getattr(cadquery, "__file__", None),
        "version": getattr(cadquery, "__version__", "unknown"),
        "reason": None,
    }


async def openscad_export_stl(
    scad_path: Path,
    stl_path: Path,
    *,
    binary: str = "openscad",
    timeout_s: float = 120.0,
) -> dict:
    info = openscad_info(binary)
    if not info["available"]:
        return {"status": "NOT_AVAILABLE", "ok": False, "error": info["reason"]}
    cmd = [binary, "-o", str(stl_path), str(scad_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        return {"status": "NOT_AVAILABLE", "ok": False, "error": str(exc)}
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"status": "FAILED", "ok": False, "error": f"OpenSCAD exceeded {timeout_s}s"}
    ok = proc.returncode == 0 and stl_path.is_file()
    return {
        "status": "PASS" if ok else "FAILED",
        "ok": ok,
        "returncode": proc.returncode,
        "stdout": out.decode(errors="replace")[-2000:],
        "stderr": err.decode(errors="replace")[-2000:],
    }


def cadquery_export(spec: DesignSpec, step_path: Path, stl_path: Path) -> dict:
    """STEP export path. Requires CadQuery; reports NOT_AVAILABLE otherwise."""
    info = cadquery_info()
    if not info["available"]:
        return {"status": "NOT_AVAILABLE", "ok": False, "error": info["reason"], "step": None, "stl": None}
    import cadquery as cq  # noqa: PLC0415

    result = None
    for feature in spec.features:
        p = feature.primitive
        if p.kind == "box":
            x, y, z = p.size_mm
            solid = cq.Workplane("XY").box(x, y, z, centered=(p.center, p.center, p.center))
        elif p.kind == "cylinder":
            d, h = p.size_mm
            solid = cq.Workplane("XY").circle(d / 2).extrude(h, both=p.center)
        else:
            solid = cq.Workplane("XY").sphere(p.size_mm[0] / 2)
        rx, ry, rz = feature.transform.rotate_deg
        if rx:
            solid = solid.rotate((0, 0, 0), (1, 0, 0), rx)
        if ry:
            solid = solid.rotate((0, 0, 0), (0, 1, 0), ry)
        if rz:
            solid = solid.rotate((0, 0, 0), (0, 0, 1), rz)
        tx, ty, tz = feature.transform.translate_mm
        if tx or ty or tz:
            solid = solid.translate((tx, ty, tz))
        if result is None:
            result = solid
        elif feature.operation == "add":
            result = result.union(solid)
        elif feature.operation == "cut":
            result = result.cut(solid)
        else:
            result = result.intersect(solid)
    try:
        cq.exporters.export(result, str(step_path))
        cq.exporters.export(result, str(stl_path))
    except Exception as exc:
        return {"status": "FAILED", "ok": False, "error": f"{type(exc).__name__}: {exc}", "step": None, "stl": None}
    return {"status": "PASS", "ok": True, "step": str(step_path), "stl": str(stl_path), "error": None}


def write_scad(spec: DesignSpec, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(compile_scad(spec), encoding="utf-8")
    return path
