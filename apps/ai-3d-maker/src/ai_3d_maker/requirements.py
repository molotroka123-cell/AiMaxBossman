"""The requirement gate: ambiguity and precision checks before any CAD runs.

Refusing early is cheaper than printing the wrong part. The gate is the place
where "two M4 holes" is allowed to be an unanswered question instead of a
silent guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import PrinterProfile
from .spec import DesignSpec
from .tolerance import CalibrationProfile

# Compensation is zero until a measured profile is explicitly selected, and
# this app applies none even then: nominal CAD geometry stays nominal, and the
# profile is used to answer "can this process hold that tolerance at all".
NO_CALIBRATION = {
    "source": "none",
    "profile_id": None,
    "version": None,
    "measured_process_tolerance_mm": None,
    "compensation_applied": False,
}

READY = "READY_FOR_CAD"
BLOCKED = "NEEDS_CLARIFICATION_OR_PROCESS_CHANGE"
NEEDS_CALIBRATION = "NEEDS_CALIBRATION_OR_DIFFERENT_PROCESS"


@dataclass(slots=True)
class RequirementGate:
    ready: bool
    status: str
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Where the capability number came from, and whether any compensation was
    # applied. Never omitted: "we did not say" is how an unbacked number gets
    # mistaken for a measurement.
    calibration: dict = field(default_factory=lambda: dict(NO_CALIBRATION))

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "status": self.status,
            "questions": self.questions,
            "warnings": self.warnings,
            "calibration": self.calibration,
        }


def evaluate_requirements(
    spec: DesignSpec,
    calibrated_tolerance_mm: float | None = None,
    *,
    profile: PrinterProfile | None = None,
    calibration: CalibrationProfile | None = None,
) -> RequirementGate:
    """Decide whether this spec may reach CAD at all.

    `calibration` is the measured route: a profile that names the printer,
    nozzle, material, layer height, line width, coupon numbers and version
    behind its capability figure. `calibrated_tolerance_mm` is the legacy bare
    number a caller can assert with nothing behind it — it is still honoured,
    and it is labelled as an unverified assertion wherever it is used.
    """
    questions = list(spec.unresolved_questions)
    warnings: list[str] = []
    status = BLOCKED
    calibration_info = dict(NO_CALIBRATION)

    if calibration is not None:
        calibrated_tolerance_mm = calibration.measured_process_tolerance_mm
        calibration_info = {
            "source": "measured_profile",
            "profile_id": calibration.id,
            "version": calibration.version,
            "measured_process_tolerance_mm": calibration.measured_process_tolerance_mm,
            "compensation_applied": False,
            "measured_at": calibration.measured_at,
            "material": calibration.material,
            "nozzle_mm": calibration.nozzle_mm,
            "layer_height_mm": calibration.layer_height_mm,
            "line_width_mm": calibration.line_width_mm,
        }
        if profile is not None and not calibration.describes(profile.id):
            questions.append(
                f"Calibration profile {calibration.id!r} was measured on another printer "
                f"({calibration.printer_profile_id!r}), not on {profile.id!r}; a measured "
                "capability does not transfer between machines."
            )
        requested_material = spec.manufacturing.material.strip().upper()
        if calibration.material.strip().upper() != requested_material:
            warnings.append(
                f"calibration was measured on {calibration.material} but this part asks for "
                f"{spec.manufacturing.material}; the capability figure is not transferable "
                "between materials."
            )
        if profile is not None and abs(calibration.nozzle_mm - profile.nozzle) > 1e-9:
            warnings.append(
                f"calibration was measured with a {calibration.nozzle_mm:g} mm nozzle but the "
                f"printer profile declares {profile.nozzle:g} mm; the capability figure does "
                "not carry over to a different nozzle."
            )
    elif calibrated_tolerance_mm is not None:
        calibration_info = {
            "source": "caller_assertion_unverified",
            "profile_id": None,
            "version": None,
            "measured_process_tolerance_mm": calibrated_tolerance_mm,
            "compensation_applied": False,
        }

    req = spec.manufacturing.required_tolerance_mm
    if req is not None:
        if req <= 0:
            questions.append("Required tolerance must be a positive number of millimetres.")
        elif calibrated_tolerance_mm is None:
            warnings.append(
                f"Tolerance +/-{req:g} mm requested but no measured calibration profile is selected; "
                "nominal CAD dimensions are not a promise about printed size."
            )
        else:
            if calibration is None:
                warnings.append(
                    f"the process capability +/-{calibrated_tolerance_mm:g} mm was supplied by the "
                    "caller and is not backed by a measured calibration profile."
                )
            if req < calibrated_tolerance_mm:
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
        return RequirementGate(False, status, questions, warnings, calibration_info)
    return RequirementGate(True, READY, [], warnings, calibration_info)
