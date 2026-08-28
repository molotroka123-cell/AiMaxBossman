"""Calibration profiles: measured process capability, kept separate from CAD nominals.

Nothing in this module is used unless a calibration profile is explicitly
selected. The default compensation is zero, because an uncalibrated printer
has no measured capability to compensate for.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class CalibrationProfile:
    id: str
    printer_profile_id: str
    material: str
    nozzle_mm: float
    layer_height_mm: float
    measured_process_tolerance_mm: float
    xy_scale: float = 1.0
    z_scale: float = 1.0
    hole_compensation_mm: float = 0.0
    filament_brand: str = ""
    measured_at: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.measured_process_tolerance_mm <= 0:
            raise ValueError("measured_process_tolerance_mm must be positive")
        for name in ("xy_scale", "z_scale"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationProfile":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def as_dict(self) -> dict:
        return asdict(self)


def scale_from_coupon(nominal_mm: float, measured_mm: float) -> float:
    """Correction factor for a printed coupon measured with calipers after cooling."""
    if nominal_mm <= 0 or measured_mm <= 0:
        raise ValueError("nominal and measured dimensions must be positive")
    return nominal_mm / measured_mm


def suggest_xy_scale(nominal_x: float, measured_x: float, nominal_y: float, measured_y: float) -> float:
    return (scale_from_coupon(nominal_x, measured_x) + scale_from_coupon(nominal_y, measured_y)) / 2.0


def coupon_spec(size_mm: float = 20.0, hole_mm: float = 5.0) -> dict:
    """A DesignSpec payload for a calibration coupon: a cube with one through hole."""
    return {
        "name": f"calibration_coupon_{size_mm:g}mm",
        "features": [
            {"primitive": {"id": "body", "kind": "box", "size_mm": [size_mm, size_mm, size_mm / 4.0]},
             "operation": "add"},
            {"primitive": {"id": "hole", "kind": "cylinder", "size_mm": [hole_mm, size_mm / 4.0]},
             "transform": {"translate_mm": [size_mm / 2.0, size_mm / 2.0, 0.0], "rotate_deg": [0, 0, 0]},
             "operation": "cut"},
        ],
        "manufacturing": {"material": "PLA", "supports_allowed": False},
        "critical_dimensions": {"outer_xy": size_mm, "hole_nominal": hole_mm},
        "assumptions": [
            "Measure X, Y, Z and the hole with calipers after the part has fully cooled.",
        ],
    }
