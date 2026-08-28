"""DesignSpec: the constrained feature DSL.

The AI never runs code on the host in the normal path. It emits this JSON, the
schema validates it, and a deterministic compiler turns it into geometry. That
boundary is the reason `cad.execute_arbitrary_code` stays denied.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FEATURES = 256
MAX_DIMENSION_MM = 10_000.0


class FitIntent(StrEnum):
    NONE = "none"
    CLEARANCE = "clearance"
    SLIDING = "sliding"
    PRESS = "press"


class Primitive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    kind: Literal["box", "cylinder", "sphere"]
    size_mm: list[float] = Field(min_length=1, max_length=3)
    center: bool = False
    segments: int | None = Field(default=None, ge=8, le=512)

    @model_validator(mode="after")
    def validate_dims(self):
        if any(v <= 0 for v in self.size_mm):
            raise ValueError("dimensions must be positive")
        if any(v > MAX_DIMENSION_MM for v in self.size_mm):
            raise ValueError(f"dimensions above {MAX_DIMENSION_MM} mm are rejected as implausible")
        expected = {"box": 3, "cylinder": 2, "sphere": 1}[self.kind]
        if len(self.size_mm) != expected:
            hint = {"box": "x,y,z", "cylinder": "diameter,height", "sphere": "diameter"}[self.kind]
            raise ValueError(f"{self.kind} needs {expected} dimension(s): {hint}")
        return self


class Transform(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translate_mm: list[float] = Field(default=[0.0, 0.0, 0.0], min_length=3, max_length=3)
    rotate_deg: list[float] = Field(default=[0.0, 0.0, 0.0], min_length=3, max_length=3)

    @model_validator(mode="after")
    def finite(self):
        for group, name in ((self.translate_mm, "translate_mm"), (self.rotate_deg, "rotate_deg")):
            for v in group:
                if v != v or v in (float("inf"), float("-inf")):
                    raise ValueError(f"{name} must be finite")
        return self


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primitive: Primitive
    transform: Transform = Transform()
    operation: Literal["add", "cut", "intersect"] = "add"


class ManufacturingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material: str = "PLA"
    required_tolerance_mm: float | None = None
    fit_intent: FitIntent = FitIntent.NONE
    supports_allowed: bool = True
    preferred_orientation: str = "auto"
    min_wall_mm: float | None = None


class DesignSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    units: Literal["mm"] = "mm"
    features: list[Feature] = Field(min_length=1, max_length=MAX_FEATURES)
    manufacturing: ManufacturingIntent = ManufacturingIntent()
    critical_dimensions: dict[str, float] = {}
    assumptions: list[str] = []
    unresolved_questions: list[str] = []

    @model_validator(mode="after")
    def structural_rules(self):
        ids = [f.primitive.id for f in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("feature ids must be unique")
        if self.features[0].operation != "add":
            raise ValueError("the first feature must be an 'add': there is nothing to cut from yet")
        return self

    def canonical_json(self) -> str:
        """Stable serialisation used for the spec digest and deterministic export."""
        return self.model_dump_json(exclude_none=False)
