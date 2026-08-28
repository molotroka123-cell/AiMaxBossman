"""Honest capability probing.

Everything optional is probed at call time and reported with a reason. The
contract: if a capability is not available, every route that needs it answers
NOT_AVAILABLE with this reason attached — it never degrades silently into a
plausible-looking fake result.
"""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass, field

from . import __version__
from .cad import csg
from .cad.external import cadquery_info, openscad_info
from .config import Settings
from .slicer import curaengine_info, prusaslicer_info


@dataclass(slots=True)
class Capability:
    name: str
    available: bool
    kind: str  # python-module | binary | builtin
    version: str | None = None
    path: str | None = None
    reason: str | None = None
    enables: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "available": self.available,
            "kind": self.kind,
            "version": self.version,
            "path": self.path,
            "reason": self.reason,
            "enables": self.enables,
        }


def _module(name: str, enables: list[str]) -> Capability:
    try:
        module = __import__(name)
    except Exception as exc:
        return Capability(name, False, "python-module", reason=f"import failed: {exc}", enables=enables)
    version = getattr(module, "__version__", None)
    if version is None:
        try:
            from importlib.metadata import version as _v

            version = _v(name)
        except Exception:
            version = "unknown"
    return Capability(
        name, True, "python-module",
        version=str(version), path=getattr(module, "__file__", None), enables=enables,
    )


def _binary(name: str, binary: str, enables: list[str]) -> Capability:
    path = shutil.which(binary)
    return Capability(
        name, path is not None, "binary", path=path,
        reason=None if path else f"{binary!r} not found on PATH", enables=enables,
    )


def probe(settings: Settings) -> list[Capability]:
    caps: list[Capability] = [
        Capability(
            "mesh-core", True, "builtin", version=__version__,
            enables=["stl-parse", "stl-export", "manifold-check", "repair", "printability", "gcode-scan"],
        ),
        _module("numpy", ["csg-backend-bridge"]),
        _module("manifold3d", ["boolean-csg", "multi-feature-designspec"]),
        _module("trimesh", ["independent-mesh-cross-check"]),
    ]

    cq = cadquery_info()
    caps.append(Capability(
        "cadquery", bool(cq["available"]), "python-module",
        version=cq.get("version"), path=cq.get("path"), reason=cq.get("reason"),
        enables=["step-export"],
    ))

    scad = openscad_info(settings.openscad_bin)
    caps.append(Capability(
        "openscad", bool(scad["available"]), "binary", path=scad.get("path"),
        reason=scad.get("reason"), enables=["external-scad-render"],
    ))

    cura = curaengine_info(settings.curaengine_bin, settings.cura_definition)
    caps.append(Capability(
        "curaengine", bool(cura["available"]), "binary", path=cura.get("path"),
        reason=cura.get("reason"), enables=["slicing", "gcode-generation"],
    ))

    prusa = prusaslicer_info(settings.prusaslicer_bin)
    caps.append(Capability(
        "prusaslicer", bool(prusa["available"]), "binary", path=prusa.get("path"),
        reason=prusa.get("reason"), enables=["slicing", "gcode-generation"],
    ))

    caps.append(Capability(
        "physical-printer",
        available=False,
        kind="hardware",
        reason=(
            "no ELEGOO Neptune 3 Plus is attached to this host; every physical stage is "
            "BLOCKED BY HARDWARE and only the simulator transport has been exercised"
        ),
        enables=["physical-print"],
    ))
    return caps


def capability_map(settings: Settings) -> dict:
    caps = probe(settings)
    by_name = {c.name: c for c in caps}
    csg_backend = csg.available_backend()
    features = {
        "designspec_single_primitive": True,
        "designspec_boolean": csg_backend.available,
        "stl_import": True,
        "mesh_validation": True,
        "mesh_repair": True,
        "printability_check": True,
        "deterministic_stl_export": True,
        "step_export": by_name["cadquery"].available,
        "openscad_render": by_name["openscad"].available,
        "slicing": by_name["curaengine"].available or by_name["prusaslicer"].available,
        "gcode_safety_scan": True,
        "print_dry_run": True,
        "physical_print": False,
    }
    return {
        "app": "ai-3d-maker",
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "csg_backend": csg_backend.as_dict(),
        "capabilities": [c.as_dict() for c in caps],
        "features": features,
        "physical_actions": {
            "allowed_by_config": settings.allow_physical_print,
            "transport": settings.printer_transport,
            "requires_human_confirmation": True,
            "note": "physical print start is never automatic and never implied by a successful build",
        },
    }
