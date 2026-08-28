"""Printer profile loader.

The seed profile separates `verified_machine_limits` (published by ELEGOO for
the Neptune 3 Plus) from `process_defaults_unverified` (this application's
conservative guesses). That separation is load-bearing and is preserved here:
the two groups never merge into one flat namespace, and every consumer has to
say which one it is reading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VerifiedMachineLimits:
    """Manufacturer-published hard limits. Treated as safety caps."""

    build_x_mm: float
    build_y_mm: float
    build_z_mm: float
    platform_x_mm: float
    platform_y_mm: float
    stock_nozzle_mm: float
    max_nozzle_temp_c: int
    max_bed_temp_c: int


@dataclass(frozen=True, slots=True)
class ProcessDefaultsUnverified:
    """App defaults. NOT manufacturer guarantees, NOT measured capability."""

    nominal_layer_height_mm: float = 0.2
    nominal_line_width_mm: float = 0.4
    minimum_wall_lines: int = 3
    note: str = ""


@dataclass(frozen=True, slots=True)
class CoordinatePolicy:
    origin: str = "front-left"
    x_min: float = 0.0
    y_min: float = 0.0
    z_min: float = 0.0
    fit_margin_mm: float = 2.0


@dataclass(frozen=True, slots=True)
class PrinterProfile:
    id: str
    manufacturer: str
    model: str
    technology: str
    verified: VerifiedMachineLimits
    process_defaults_unverified: ProcessDefaultsUnverified
    coordinate_policy: CoordinatePolicy
    transfer: tuple[str, ...] = ()
    manufacturer_model_formats: tuple[str, ...] = ()
    manufacturer_filaments: tuple[str, ...] = ()
    source_path: str = ""

    # ---- convenience accessors over VERIFIED limits only -----------------
    @property
    def build_x(self) -> float:
        return self.verified.build_x_mm

    @property
    def build_y(self) -> float:
        return self.verified.build_y_mm

    @property
    def build_z(self) -> float:
        return self.verified.build_z_mm

    @property
    def nozzle(self) -> float:
        return self.verified.stock_nozzle_mm

    @property
    def max_nozzle_temp(self) -> int:
        return self.verified.max_nozzle_temp_c

    @property
    def max_bed_temp(self) -> int:
        return self.verified.max_bed_temp_c

    @property
    def fit_margin(self) -> float:
        return self.coordinate_policy.fit_margin_mm

    def usable_xyz(self) -> tuple[float, float, float]:
        """Build volume minus a safety margin in X/Y. Z is not shrunk."""
        m = self.fit_margin
        return (
            max(0.0, self.build_x - 2 * m),
            max(0.0, self.build_y - 2 * m),
            self.build_z,
        )

    def fits(self, bbox_xyz, *, allow_rotate: bool = True) -> tuple[bool, tuple[float, float, float] | None]:
        usable = self.usable_xyz()
        if any(v <= 0 for v in bbox_xyz):
            return False, None
        candidates = sorted(set(permutations(tuple(float(v) for v in bbox_xyz), 3))) if allow_rotate else [tuple(bbox_xyz)]
        # Prefer the identity orientation when it already fits.
        identity = tuple(float(v) for v in bbox_xyz)
        ordered = ([identity] + [c for c in candidates if c != identity]) if allow_rotate else candidates
        for xyz in ordered:
            if all(v <= limit + 1e-9 for v, limit in zip(xyz, usable)):
                return True, xyz
        return False, None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "technology": self.technology,
            "verified_machine_limits": {
                "build_x_mm": self.verified.build_x_mm,
                "build_y_mm": self.verified.build_y_mm,
                "build_z_mm": self.verified.build_z_mm,
                "platform_x_mm": self.verified.platform_x_mm,
                "platform_y_mm": self.verified.platform_y_mm,
                "stock_nozzle_mm": self.verified.stock_nozzle_mm,
                "max_nozzle_temp_c": self.verified.max_nozzle_temp_c,
                "max_bed_temp_c": self.verified.max_bed_temp_c,
            },
            "process_defaults_unverified": {
                "nominal_layer_height_mm": self.process_defaults_unverified.nominal_layer_height_mm,
                "nominal_line_width_mm": self.process_defaults_unverified.nominal_line_width_mm,
                "minimum_wall_lines": self.process_defaults_unverified.minimum_wall_lines,
                "note": self.process_defaults_unverified.note,
            },
            "coordinate_policy": {
                "origin": self.coordinate_policy.origin,
                "x_min": self.coordinate_policy.x_min,
                "y_min": self.coordinate_policy.y_min,
                "z_min": self.coordinate_policy.z_min,
                "fit_margin_mm": self.coordinate_policy.fit_margin_mm,
            },
            "transfer": list(self.transfer),
            "manufacturer_model_formats": list(self.manufacturer_model_formats),
            "manufacturer_filaments": list(self.manufacturer_filaments),
        }

    @classmethod
    def load(cls, path: str | Path) -> "PrinterProfile":
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if "verified_machine_limits" not in data:
            raise ValueError(f"{p}: profile is missing verified_machine_limits")
        m = data["verified_machine_limits"]
        pd = data.get("process_defaults_unverified", {})
        cp = data.get("coordinate_policy", {})
        return cls(
            id=str(data["id"]),
            manufacturer=str(data.get("manufacturer", "")),
            model=str(data["model"]),
            technology=str(data.get("technology", "FDM")),
            verified=VerifiedMachineLimits(
                build_x_mm=float(m["build_x_mm"]),
                build_y_mm=float(m["build_y_mm"]),
                build_z_mm=float(m["build_z_mm"]),
                platform_x_mm=float(m["platform_x_mm"]),
                platform_y_mm=float(m["platform_y_mm"]),
                stock_nozzle_mm=float(m["stock_nozzle_mm"]),
                max_nozzle_temp_c=int(m["max_nozzle_temp_c"]),
                max_bed_temp_c=int(m["max_bed_temp_c"]),
            ),
            process_defaults_unverified=ProcessDefaultsUnverified(
                nominal_layer_height_mm=float(pd.get("nominal_layer_height_mm", 0.2)),
                nominal_line_width_mm=float(pd.get("nominal_line_width_mm", 0.4)),
                minimum_wall_lines=int(pd.get("minimum_wall_lines", 3)),
                note=str(pd.get("note", "")),
            ),
            coordinate_policy=CoordinatePolicy(
                origin=str(cp.get("origin", "front-left")),
                x_min=float(cp.get("x_min", 0.0)),
                y_min=float(cp.get("y_min", 0.0)),
                z_min=float(cp.get("z_min", 0.0)),
                fit_margin_mm=float(cp.get("fit_margin_mm", 2.0)),
            ),
            transfer=tuple(data.get("transfer", ())),
            manufacturer_model_formats=tuple(data.get("manufacturer_model_formats", ())),
            manufacturer_filaments=tuple(data.get("manufacturer_filaments", ())),
            source_path=str(p),
        )


def load_material_defaults(path: str | Path) -> dict:
    """Starting temperature presets. Explicitly unverified for any filament brand."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
