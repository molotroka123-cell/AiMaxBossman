"""The requirement gate: ambiguity and precision checks before any CAD runs.

Refusing early is cheaper than printing the wrong part. The gate is the place
where "two M4 holes" is allowed to be an unanswered question instead of a
silent guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import PrinterProfile
from .spec import DesignSpec

READY = "READY_FOR_CAD"
BLOCKED = "NEEDS_CLARIFICATION_OR_PROCESS_CHANGE"
NEEDS_CALIBRATION = "NEEDS_CALIBRATION_OR_DIFFERENT_PROCESS"


@dataclass(slots=True)
class RequirementGate:
    ready: bool
    status: str
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "status": self.status,
            "questions": self.questions,
            "warnings": self.warnings,
        }


def evaluate_requirements(
    spec: DesignSpec,
    calibrated_tolerance_mm: float | None = None,
    *,
    profile: PrinterProfile | None = None,
) -> RequirementGate:
    questions = list(spec.unresolved_questions)
    warnings: list[str] = []
    status = BLOCKED

    req = spec.manufacturing.required_tolerance_mm
    if req is not None:
        if req <= 0:
            questions.append("Required tolerance must be a positive number of millimetres.")
        elif calibrated_tolerance_mm is None:
            warnings.append(
                f"Tolerance +/-{req:g} mm requested but no measured calibration profile is selected; "
                "nominal CAD dimensions are not a promise about printed size."
            )
        elif req < calibrated_tolerance_mm:
            questions.append(
                f"Requested +/-{req:g} mm is tighter than the calibrated process capability "
                f"+/-{calibrated_tolerance_mm:g} mm."
            )
            status = NEEDS_CALIBRATION

    if spec.manufacturing.fit_intent.value != "none" and req is None:
        warnings.append(
            f"fit_intent={spec.manufacturing.fit_intent.value} implies a tolerance requirement, "
            "but required_tolerance_mm is unset."
        )

    if profile is not None:
        material = spec.manufacturing.material.strip().upper()
        known = {m.upper() for m in profile.manufacturer_filaments}
        if known and material not in known:
            warnings.append(
                f"material {spec.manufacturing.material!r} is not in the manufacturer's listed set "
                f"{sorted(profile.manufacturer_filaments)} for the {profile.model}."
            )
        min_wall = spec.manufacturing.min_wall_mm
        if min_wall is not None:
            target = profile.nozzle * profile.process_defaults_unverified.minimum_wall_lines
            if min_wall < target:
                warnings.append(
                    f"requested minimum wall {min_wall:g} mm is below the conservative app default "
                    f"{target:g} mm ({profile.process_defaults_unverified.minimum_wall_lines} lines "
                    f"at {profile.nozzle:g} mm nozzle)."
                )

    if questions:
        return RequirementGate(False, status, questions, warnings)
    return RequirementGate(True, READY, [], warnings)
