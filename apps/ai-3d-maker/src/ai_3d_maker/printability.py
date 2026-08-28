"""Units, scale, orientation, bed placement and the printability verdict.

This is the module that decides whether an STL may be called "printable". The
rule it enforces: a file existing is not evidence. The mesh must be closed,
manifold, consistently wound, dimensionally plausible, and it must physically
fit inside the printer's verified build volume in some orientation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

from .mesh import Mesh
from .meshcheck import MeshReport
from .profile import PrinterProfile

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "mil": 0.0254}

# Below/above these the declared units are almost certainly wrong.
SUSPICIOUS_MIN_EXTENT_MM = 0.5
SUSPICIOUS_MAX_EXTENT_MM = 2000.0


@dataclass(slots=True)
class UnitsReport:
    declared: str
    factor_to_mm: float
    converted: bool
    extents_mm: tuple[float, float, float]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "declared": self.declared,
            "factor_to_mm": self.factor_to_mm,
            "converted": self.converted,
            "extents_mm": list(self.extents_mm),
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class FitReport:
    fits: bool
    usable_volume_mm: tuple[float, float, float]
    model_extents_mm: tuple[float, float, float]
    chosen_orientation_mm: tuple[float, float, float] | None
    axis_permutation: tuple[int, int, int] | None
    rotated: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fits": self.fits,
            "usable_volume_mm": list(self.usable_volume_mm),
            "model_extents_mm": list(self.model_extents_mm),
            "chosen_orientation_mm": list(self.chosen_orientation_mm) if self.chosen_orientation_mm else None,
            "axis_permutation": list(self.axis_permutation) if self.axis_permutation else None,
            "rotated": self.rotated,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class PrintabilityVerdict:
    printable: bool
    status: str  # PRINTABLE | PRINTABLE_WITH_WARNINGS | NOT_PRINTABLE
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "printable": self.printable,
            "status": self.status,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "checks": self.checks,
        }


# ------------------------------------------------------------------- units
def normalize_units(mesh: Mesh, declared_units: str = "mm") -> tuple[Mesh, UnitsReport]:
    unit = (declared_units or "mm").strip().lower()
    if unit not in UNIT_TO_MM:
        raise ValueError(f"unknown unit {declared_units!r}; supported: {sorted(UNIT_TO_MM)}")
    factor = UNIT_TO_MM[unit]
    converted = factor != 1.0
    out = mesh.scaled(factor) if converted else mesh.copy()
    out.units = "mm"
    extents = out.extents()
    warnings: list[str] = []
    largest = max(extents) if extents else 0.0
    if 0.0 < largest < SUSPICIOUS_MIN_EXTENT_MM:
        warnings.append(
            f"largest dimension is {largest:g} mm after unit conversion; the source file may not be in {unit}"
        )
    if largest > SUSPICIOUS_MAX_EXTENT_MM:
        warnings.append(
            f"largest dimension is {largest:g} mm after unit conversion; the source file may be in a smaller unit"
        )
    return out, UnitsReport(unit, factor, converted, extents, warnings)


def apply_scale(mesh: Mesh, scale: float | tuple[float, float, float]) -> Mesh:
    return mesh.scaled(scale)


def scale_to_fit(mesh: Mesh, profile: PrinterProfile, *, headroom: float = 1.0) -> tuple[Mesh, float]:
    """Uniformly shrink until the model fits. Returns the mesh and factor used."""
    usable = profile.usable_xyz()
    extents = sorted(mesh.extents(), reverse=True)
    limits = sorted(usable, reverse=True)
    factor = 1.0
    for e, limit in zip(extents, limits):
        if e > 0:
            factor = min(factor, (limit * headroom) / e)
    if factor >= 1.0:
        return mesh.copy(), 1.0
    return mesh.scaled(factor), factor


# ------------------------------------------------------------- orientation
def evaluate_fit(mesh: Mesh, profile: PrinterProfile, *, allow_rotate: bool = True) -> FitReport:
    extents = mesh.extents()
    usable = profile.usable_xyz()
    if any(e <= 0 for e in extents):
        return FitReport(
            False, usable, extents, None, None, False,
            errors=[f"model has a zero-thickness axis {extents}: nothing to print"],
        )
    identity = (0, 1, 2)
    orders = [identity] + ([p for p in sorted(permutations(range(3))) if p != identity] if allow_rotate else [])
    for order in orders:
        candidate = (extents[order[0]], extents[order[1]], extents[order[2]])
        if all(v <= limit + 1e-9 for v, limit in zip(candidate, usable)):
            warnings: list[str] = []
            if order != identity:
                warnings.append(f"model only fits after reorienting axes {order}")
            return FitReport(True, usable, extents, candidate, order, order != identity, warnings=warnings)
    return FitReport(
        False, usable, extents, None, None, False,
        errors=[
            f"model {extents[0]:.2f}x{extents[1]:.2f}x{extents[2]:.2f} mm does not fit the usable build "
            f"volume {usable[0]:.1f}x{usable[1]:.1f}x{usable[2]:.1f} mm of the {profile.model}"
        ],
    )


def orient_mesh(mesh: Mesh, axis_permutation: tuple[int, int, int]) -> Mesh:
    if tuple(axis_permutation) == (0, 1, 2):
        return mesh.copy()
    return mesh.rotated_axis_swap(tuple(axis_permutation))


def place_on_bed(mesh: Mesh, profile: PrinterProfile, *, center_xy: bool = True) -> Mesh:
    """Drop the model onto Z=0 and centre it in the usable XY area."""
    lo, hi = mesh.bounds()
    dz = -lo[2] + profile.coordinate_policy.z_min
    if center_xy:
        width = hi[0] - lo[0]
        depth = hi[1] - lo[1]
        target_x = profile.coordinate_policy.x_min + (profile.build_x - width) / 2.0
        target_y = profile.coordinate_policy.y_min + (profile.build_y - depth) / 2.0
        dx = target_x - lo[0]
        dy = target_y - lo[1]
    else:
        dx = profile.coordinate_policy.x_min + profile.fit_margin - lo[0]
        dy = profile.coordinate_policy.y_min + profile.fit_margin - lo[1]
    return mesh.translated((dx, dy, dz))


# ----------------------------------------------------------- feature checks
def minimum_wall_warning(wall_mm: float, nozzle_mm: float, minimum_lines: int = 3) -> list[str]:
    target = nozzle_mm * minimum_lines
    if wall_mm + 1e-9 >= target:
        return []
    return [
        f"wall {wall_mm:g} mm is below the conservative {minimum_lines}-line target "
        f"{target:g} mm for a {nozzle_mm:g} mm nozzle"
    ]


def thin_feature_warnings(mesh: Mesh, profile: PrinterProfile) -> list[str]:
    """Coarse proxy only: bounding-box thinness, not true wall measurement.

    A real minimum-wall analysis needs the slicer. This flags the obvious case
    where the whole part is thinner than a few extrusion lines.
    """
    warnings: list[str] = []
    smallest = min(mesh.extents())
    pd = profile.process_defaults_unverified
    target = profile.nozzle * pd.minimum_wall_lines
    if 0 < smallest < target:
        warnings.append(
            f"smallest overall dimension {smallest:.2f} mm is below {target:.2f} mm "
            f"({pd.minimum_wall_lines} lines at a {profile.nozzle:g} mm nozzle); "
            "this is an unverified app default, not a manufacturer limit"
        )
    if smallest < profile.process_defaults_unverified.nominal_layer_height_mm:
        warnings.append(
            f"smallest overall dimension {smallest:.3f} mm is below one nominal layer "
            f"({pd.nominal_layer_height_mm:g} mm)"
        )
    return warnings


# ---------------------------------------------------------------- verdict
def decide_printability(
    mesh_report: MeshReport,
    fit_report: FitReport,
    *,
    extra_warnings: list[str] | None = None,
    allow_multiple_components: bool = True,
) -> PrintabilityVerdict:
    reasons: list[str] = []
    warnings: list[str] = list(extra_warnings or [])

    if mesh_report.triangles == 0:
        reasons.append("mesh has no triangles")
    if not mesh_report.is_watertight:
        reasons.append(
            "mesh is not watertight: "
            + (f"{mesh_report.boundary_edges} open edge(s)" if mesh_report.boundary_edges else "surface is not closed")
        )
    if not mesh_report.is_edge_manifold:
        reasons.append(f"mesh is not manifold: {mesh_report.non_manifold_edges} over-shared edge(s)")
    if not mesh_report.is_winding_consistent:
        reasons.append(f"face winding is inconsistent on {mesh_report.inconsistent_winding_edges} edge(s)")
    if mesh_report.is_watertight and mesh_report.signed_volume_mm3 <= 0:
        reasons.append("closed mesh encloses non-positive volume")
    if not fit_report.fits:
        reasons.extend(fit_report.errors)

    if mesh_report.degenerate_triangles:
        warnings.append(f"{mesh_report.degenerate_triangles} degenerate triangle(s) remained after repair")
    if mesh_report.duplicate_triangles:
        warnings.append(f"{mesh_report.duplicate_triangles} duplicate triangle(s) remained after repair")
    if mesh_report.components > 1:
        message = f"model consists of {mesh_report.components} disconnected components"
        if allow_multiple_components:
            warnings.append(message + "; they will print as separate objects")
        else:
            reasons.append(message)
    warnings.extend(fit_report.warnings)

    if reasons:
        return PrintabilityVerdict(False, "NOT_PRINTABLE", reasons, warnings, _checks(mesh_report, fit_report))
    status = "PRINTABLE_WITH_WARNINGS" if warnings else "PRINTABLE"
    return PrintabilityVerdict(True, status, [], warnings, _checks(mesh_report, fit_report))


def _checks(mesh_report: MeshReport, fit_report: FitReport) -> dict:
    return {
        "watertight": mesh_report.is_watertight,
        "manifold": mesh_report.is_edge_manifold,
        "winding_consistent": mesh_report.is_winding_consistent,
        "degenerate_triangles": mesh_report.degenerate_triangles,
        "duplicate_triangles": mesh_report.duplicate_triangles,
        "components": mesh_report.components,
        "extents_mm": list(mesh_report.extents_mm),
        "volume_mm3": mesh_report.signed_volume_mm3,
        "fits_build_volume": fit_report.fits,
        "usable_volume_mm": list(fit_report.usable_volume_mm),
        "self_intersection_check": mesh_report.self_intersection_check,
    }
