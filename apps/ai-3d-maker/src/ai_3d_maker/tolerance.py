"""Calibration profiles: measured process capability, kept separate from CAD nominals.

Nothing in this module is used unless a calibration profile is explicitly
selected. The default compensation is zero, because an uncalibrated printer
has no measured capability to compensate for.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CalibrationProfile:
    """A measured capability, tied to the exact process that produced it.

    "+/- 0.15 mm" is not a property of a printer. It is a property of one
    printer with one nozzle running one material at one layer height and one
    line width, measured on a specific coupon on a specific date. Every one of
    those is required here, because a number without them cannot be checked,
    superseded or argued with.

    `version` exists so a re-measurement replaces an old profile instead of
    quietly averaging with it.
    """

    id: str
    printer_profile_id: str
    material: str
    nozzle_mm: float
    layer_height_mm: float
    measured_process_tolerance_mm: float
    line_width_mm: float = 0.0
    measured_at: str = ""
    version: int = 1
    # {feature name: [nominal_mm, measured_mm]} — the raw calipers reading the
    # tolerance above was derived from.
    coupon_measurements: dict = field(default_factory=dict)
    xy_scale: float = 1.0
    z_scale: float = 1.0
    hole_compensation_mm: float = 0.0
    filament_brand: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.measured_process_tolerance_mm <= 0:
            raise ValueError("measured_process_tolerance_mm must be positive")
        for name in ("xy_scale", "z_scale"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("nozzle_mm", "layer_height_mm", "line_width_mm"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive: a calibration is per process, not generic")
        if not str(self.measured_at).strip():
            raise ValueError("measured_at is required: an undated measurement cannot be superseded")
        if int(self.version) < 1:
            raise ValueError("version must be a positive integer")
        self.version = int(self.version)
        if not self.coupon_measurements:
            raise ValueError(
                "coupon_measurements is required: a tolerance with no measured coupon behind it "
                "is a guess wearing a number"
            )
        for name, pair in self.coupon_measurements.items():
            values = list(pair) if isinstance(pair, (list, tuple)) else []
            if len(values) != 2:
                raise ValueError(
                    f"coupon_measurements[{name!r}] must be [nominal_mm, measured_mm]"
                )
            if any(float(v) <= 0 for v in values):
                raise ValueError(
                    f"coupon_measurements[{name!r}] must hold two positive millimetre readings"
                )
            self.coupon_measurements[name] = [float(values[0]), float(values[1])]

    def describes(self, printer_profile_id: str) -> bool:
        return self.printer_profile_id == printer_profile_id

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
