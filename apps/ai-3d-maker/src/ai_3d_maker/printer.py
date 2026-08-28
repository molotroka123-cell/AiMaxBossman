"""The physical boundary.

Everything above this module is digital: geometry, files, checks. Everything in
this module can move a heater or a motor, so it is gated separately and
deliberately awkward to reach.

Three independent conditions must all hold before any physical action runs:

  1. the transport must be a real one (the default transport is a simulator
     that touches no hardware at all);
  2. `AI3D_ALLOW_PHYSICAL_PRINT` must be enabled in the environment;
  3. the caller must present the exact confirmation token for this job and this
     artifact digest — a human has to have looked at the specific file.

Failing any one of them is a refusal, never a downgrade to "do it anyway".
Additionally, G-code that failed the safety scan can never be sent, regardless
of confirmation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import ConfirmationRequiredError, UnsafeGcodeError
from .gcode import GCodeScan
from .profile import PrinterProfile

CONFIRMATION_PREFIX = "PRINT-CONFIRM"


class PhysicalAction(StrEnum):
    TRANSFER_TO_MEDIA = "transfer_to_media"
    PREHEAT = "preheat"
    START_PRINT = "start_print"
    MOVE_AXES = "move_axes"


class Transport(StrEnum):
    SIMULATOR = "simulator"
    TF_CARD = "tf_card"
    USB_SERIAL = "usb_serial"


# Only these transports can reach hardware. The Neptune 3 Plus, per ELEGOO,
# transfers jobs by TF card or USB cable; there is no official network print
# interface and this app does not invent one.
HARDWARE_TRANSPORTS = {Transport.TF_CARD, Transport.USB_SERIAL}


def confirmation_token(job_id: str, artifact_sha256: str) -> str:
    """Deterministic token bound to one job and one exact artifact."""
    digest = hashlib.sha256(f"{job_id}:{artifact_sha256}".encode()).hexdigest()[:16].upper()
    return f"{CONFIRMATION_PREFIX}-{digest}"


@dataclass(slots=True)
class PhysicalRequest:
    action: PhysicalAction
    job_id: str
    artifact_path: Path | None = None
    artifact_sha256: str = ""
    confirmation: str = ""
    transport: Transport = Transport.SIMULATOR


@dataclass(slots=True)
class PhysicalResult:
    status: str  # SIMULATED | DONE | REFUSED | BLOCKED_BY_HARDWARE
    action: str
    transport: str
    performed_physical_action: bool
    message: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "action": self.action,
            "transport": self.transport,
            "performed_physical_action": self.performed_physical_action,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(slots=True)
class DryRunReport:
    status: str
    layers: int
    total_lines: int
    extrusion_moves: int
    filament_mm: float
    max_nozzle_target_c: float
    max_bed_target_c: float
    envelope_min_mm: dict
    envelope_max_mm: dict
    within_envelope: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "layers": self.layers,
            "total_lines": self.total_lines,
            "extrusion_moves": self.extrusion_moves,
            "filament_mm": self.filament_mm,
            "max_nozzle_target_c": self.max_nozzle_target_c,
            "max_bed_target_c": self.max_bed_target_c,
            "envelope_min_mm": self.envelope_min_mm,
            "envelope_max_mm": self.envelope_max_mm,
            "within_envelope": self.within_envelope,
            "notes": self.notes,
        }


_LAYER_COMMENT = re.compile(r";\s*(LAYER|layer)[:\s]", re.IGNORECASE)


def dry_run(gcode_text: str, profile: PrinterProfile, scan: GCodeScan) -> DryRunReport:
    """Simulate the print without touching hardware.

    This is a kinematic/state walk, not a firmware emulation. It exists so the
    pipeline has a real, exercisable "print" stage on a machine with no printer
    attached.
    """
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
    abs_xyz = True
    abs_e = True
    filament = 0.0
    extrusion_moves = 0
    layers = 0
    total_lines = 0
    notes: list[str] = []

    for raw in gcode_text.splitlines():
        total_lines += 1
        if _LAYER_COMMENT.search(raw):
            layers += 1
        code = raw.split(";", 1)[0].strip()
        if not code:
            continue
        m = re.match(r"\s*([GM])\s*0*(\d+)", code, flags=re.IGNORECASE)
        if not m:
            continue
        cmd = f"{m.group(1).upper()}{int(m.group(2))}"
        rest = code[m.end():]
        params = {}
        for key, value in re.findall(r"([A-Za-z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", rest):
            params.setdefault(key.upper(), float(value))
        if cmd == "G90":
            abs_xyz = True
        elif cmd == "G91":
            abs_xyz = False
        elif cmd == "M82":
            abs_e = True
        elif cmd == "M83":
            abs_e = False
        elif cmd == "G92":
            for axis in ("X", "Y", "Z", "E"):
                if axis in params:
                    pos[axis] = params[axis]
        elif cmd == "G28":
            for axis in ("X", "Y", "Z"):
                pos[axis] = 0.0
        elif cmd in {"G0", "G1"}:
            previous_e = pos["E"]
            for axis in ("X", "Y", "Z"):
                if axis in params:
                    pos[axis] = params[axis] if abs_xyz else pos[axis] + params[axis]
            if "E" in params:
                pos["E"] = params["E"] if abs_e else pos["E"] + params["E"]
                delta = pos["E"] - previous_e
                if delta > 0:
                    filament += delta
                    extrusion_moves += 1

    if layers == 0:
        notes.append("no layer markers found; layer count is unknown, not zero")
    within = scan.status != "FAILED"
    if not within:
        notes.append("G-code safety scan reported errors; this job must not be sent to a printer")

    return DryRunReport(
        status="SIMULATED",
        layers=layers,
        total_lines=total_lines,
        extrusion_moves=extrusion_moves,
        filament_mm=filament,
        max_nozzle_target_c=scan.max_nozzle_target_c,
        max_bed_target_c=scan.max_bed_target_c,
        envelope_min_mm=dict(scan.extrusion_bounds_min_mm),
        envelope_max_mm=dict(scan.extrusion_bounds_max_mm),
        within_envelope=within,
        notes=notes,
    )


def execute_physical(
    request: PhysicalRequest,
    *,
    allow_physical: bool,
    scan: GCodeScan | None,
    media_dir: str = "",
) -> PhysicalResult:
    """The single funnel for every hardware-capable action."""
    action = request.action
    transport = request.transport

    # Gate 1: unsafe G-code can never be sent, no matter what else is set.
    if scan is not None and scan.status == "FAILED":
        raise UnsafeGcodeError(
            "G-code failed the safety scan; physical execution is refused",
            detail={"issues": scan.issues[:20], "profile_id": scan.profile_id},
        )

    # Gate 2: explicit human confirmation bound to this exact artifact.
    expected = confirmation_token(request.job_id, request.artifact_sha256)
    if request.confirmation != expected:
        raise ConfirmationRequiredError(
            f"physical action {action.value!r} requires explicit human confirmation",
            detail={
                "job_id": request.job_id,
                "artifact_sha256": request.artifact_sha256,
                "expected_confirmation": expected,
                "hint": "re-send this request with confirmation set to the token above",
            },
        )

    # Gate 3: the simulator never touches hardware, whatever is confirmed.
    if transport == Transport.SIMULATOR:
        return PhysicalResult(
            status="SIMULATED",
            action=action.value,
            transport=transport.value,
            performed_physical_action=False,
            message=(
                f"{action.value} accepted by the simulator transport. No heater, motor or "
                "media was touched. Set AI3D_PRINTER_TRANSPORT to a real transport to act."
            ),
            detail={"artifact": str(request.artifact_path) if request.artifact_path else None},
        )

    # Gate 4: environment must permit hardware at all.
    if not allow_physical:
        raise ConfirmationRequiredError(
            f"physical action {action.value!r} is disabled: AI3D_ALLOW_PHYSICAL_PRINT is not enabled",
            detail={"transport": transport.value},
        )

    if transport == Transport.TF_CARD:
        if action in {PhysicalAction.START_PRINT, PhysicalAction.PREHEAT, PhysicalAction.MOVE_AXES}:
            return PhysicalResult(
                status="REFUSED",
                action=action.value,
                transport=transport.value,
                performed_physical_action=False,
                message=(
                    "TF card transfer cannot start a print, preheat or move the machine. "
                    "The ELEGOO Neptune 3 Plus is started from its own screen by a person."
                ),
            )
        if not media_dir:
            return PhysicalResult(
                status="REFUSED", action=action.value, transport=transport.value,
                performed_physical_action=False,
                message="no AI3D_PRINTER_MEDIA_DIR configured for TF card transfer",
            )
        target_dir = Path(media_dir)
        if not target_dir.is_dir():
            return PhysicalResult(
                status="REFUSED", action=action.value, transport=transport.value,
                performed_physical_action=False,
                message=f"media directory {media_dir!r} is not mounted",
            )
        if request.artifact_path is None or not Path(request.artifact_path).is_file():
            return PhysicalResult(
                status="REFUSED", action=action.value, transport=transport.value,
                performed_physical_action=False,
                message="no artifact to transfer",
            )
        source = Path(request.artifact_path)
        destination = target_dir / source.name
        destination.write_bytes(source.read_bytes())
        return PhysicalResult(
            status="DONE",
            action=action.value,
            transport=transport.value,
            performed_physical_action=True,
            message=f"copied {source.name} to removable media; start the print from the printer screen",
            detail={"destination": str(destination), "sha256": request.artifact_sha256},
        )

    # USB serial streaming to a live machine is intentionally not implemented.
    return PhysicalResult(
        status="BLOCKED_BY_HARDWARE",
        action=action.value,
        transport=transport.value,
        performed_physical_action=False,
        message=(
            "USB serial control of a live ELEGOO Neptune 3 Plus is not implemented in this build "
            "and has never been exercised against the physical machine."
        ),
    )
