"""G-code safety scanner.

Generated G-code is executable machine control, not a document. This scanner is
the last digital gate before anything is handed to a physical machine.

It is not a firmware emulator and does not claim to be. It models absolute /
relative positioning, absolute / relative extrusion, G20/G21 unit selection,
G92 resets, homing and parking, temperature caps and the verified build
envelope, and it refuses commands that persist settings, restart firmware or
disable a firmware safety interlock.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .profile import PrinterProfile

_PARAM = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)")

# Commands that persist configuration or restart the machine. Slicer output for
# a normal print never needs these, so their presence is treated as an error.
BLOCKED_COMMANDS = {
    "M500": "writes settings to EEPROM",
    "M501": "reloads settings from EEPROM",
    "M502": "resets settings to factory defaults",
    "M503": "reports stored settings (unexpected in print output)",
    "M997": "firmware update / reboot style command",
    "M999": "clears an error state and resumes; unsafe in unattended print output",
    "M301": "sets and may persist hotend PID values",
    "M304": "sets and may persist bed PID values",
    "M92": "changes steps-per-unit calibration",
    "M851": "changes Z probe offset",
}

# Commands the scanner understands. Anything else is flagged in strict mode.
KNOWN_COMMANDS = {
    "G0", "G1", "G2", "G3", "G4", "G20", "G21", "G27", "G28", "G29",
    "G90", "G91", "G92",
    "M17", "M18", "M20", "M73", "M75", "M76", "M77", "M82", "M83", "M84",
    "M104", "M105", "M106", "M107", "M108", "M109", "M117", "M118",
    "M140", "M141", "M190", "M191", "M201", "M203", "M204", "M205",
    "M220", "M221", "M400", "M401", "M402", "M420", "M486", "M900",
}

# Commands that do not persist anything but disable a firmware safety
# interlock or drive hardware in a way ordinary print output never needs.
# These are flagged whatever `strict_unknown` is set to: a safety-relevant
# command is never silently ignored.
SAFETY_RELEVANT_COMMANDS = {
    "M302": ("ERROR", "disables the firmware cold-extrusion guard"),
    "M303": ("ERROR", "PID autotune heats the hotend unattended for minutes"),
    "M290": ("WARNING", "applies a babystep Z offset the scanner cannot verify"),
    "M906": ("WARNING", "changes stepper driver current"),
    "M907": ("WARNING", "changes stepper driver current"),
    "M913": ("WARNING", "changes stepper hybrid threshold"),
    "M914": ("WARNING", "changes stepper stall sensitivity (sensorless homing)"),
}

# Millimetres per unit for each G20/G21 mode.
UNIT_SCALE_MM = {"mm": 1.0, "inch": 25.4}

SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "ERROR": 2}


@dataclass(slots=True)
class GCodeIssue:
    severity: str
    line: int
    message: str
    command: str


@dataclass(slots=True)
class GCodeScan:
    status: str  # PASS | WARN | FAILED
    issues: list[dict] = field(default_factory=list)
    lines_scanned: int = 0
    commands_scanned: int = 0
    max_nozzle_target_c: float = 0.0
    max_bed_target_c: float = 0.0
    extrusion_bounds_min_mm: dict = field(default_factory=dict)
    extrusion_bounds_max_mm: dict = field(default_factory=dict)
    profile_id: str = ""
    units_mode: str = "mm"

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def safe(self) -> bool:
        return self.status != "FAILED"


def parse_params(code: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in _PARAM.findall(code):
        upper = key.upper()
        if upper in out:
            continue
        try:
            out[upper] = float(value)
        except ValueError:  # pragma: no cover - regex guarantees numeric
            continue
    return out


def _strip_comment(raw: str) -> str:
    # Marlin: ';' to end of line, and '(...)' inline comments.
    line = raw.split(";", 1)[0]
    line = re.sub(r"\([^)]*\)", " ", line)
    return line.strip()


def scan_gcode(text: str, profile: PrinterProfile, *, strict_unknown: bool = False) -> GCodeScan:
    issues: list[GCodeIssue] = []
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
    abs_xyz = True
    abs_e = True
    homed = False
    # G20/G21. Every coordinate below is converted to millimetres before it is
    # compared against the envelope: in inch mode "X13" is 330.2 mm, which is
    # off the front of a 320 mm bed.
    units_mode = "mm"
    unit_scale = UNIT_SCALE_MM["mm"]
    max_nozzle = 0.0
    max_bed = 0.0
    ext_min: dict[str, float] = {}
    ext_max: dict[str, float] = {}
    commands = 0
    lines = 0

    limits = {"X": profile.build_x, "Y": profile.build_y, "Z": profile.build_z}

    for lineno, raw in enumerate(text.splitlines(), 1):
        lines += 1
        code = _strip_comment(raw)
        if not code:
            continue
        # Normalise "G01" and "M104S205" so padded or unspaced forms cannot
        # bypass the command tables.
        m = re.match(r"\s*([GMT])\s*0*(\d+)", code, flags=re.IGNORECASE)
        if m:
            cmd = f"{m.group(1).upper()}{int(m.group(2))}"
            rest = code[m.end():]
        else:
            cmd = code.split()[0].upper()
            rest = code[len(cmd):]
        commands += 1

        if cmd in BLOCKED_COMMANDS:
            issues.append(GCodeIssue("ERROR", lineno, BLOCKED_COMMANDS[cmd], cmd))
            continue

        if cmd in SAFETY_RELEVANT_COMMANDS:
            severity, message = SAFETY_RELEVANT_COMMANDS[cmd]
            issues.append(GCodeIssue(severity, lineno, message, cmd))
            continue

        params = parse_params(rest)

        if cmd == "G20":
            units_mode = "inch"
            unit_scale = UNIT_SCALE_MM["inch"]
            issues.append(GCodeIssue(
                "INFO", lineno,
                "inch units selected; all following coordinates are read as inches",
                cmd,
            ))
            continue
        if cmd == "G21":
            units_mode = "mm"
            unit_scale = UNIT_SCALE_MM["mm"]
            continue
        if cmd == "G90":
            abs_xyz = True
            continue
        if cmd == "G91":
            abs_xyz = False
            issues.append(GCodeIssue("INFO", lineno, "relative positioning enabled", cmd))
            continue
        if cmd == "M82":
            abs_e = True
            continue
        if cmd == "M83":
            abs_e = False
            continue
        if cmd == "G28":
            homed = True
            for axis in ("X", "Y", "Z"):
                if not params or axis in params:
                    pos[axis] = 0.0
            continue
        if cmd == "G27":
            # Park. A travel move to the firmware park position; it never
            # extrudes, so there is nothing to envelope-check, but it is a
            # modelled command rather than an unknown one.
            issues.append(GCodeIssue("INFO", lineno, "toolhead parked", cmd))
            continue
        if cmd == "G92":
            for axis in ("X", "Y", "Z", "E"):
                if axis in params:
                    pos[axis] = params[axis] * unit_scale
            continue

        if cmd in {"M104", "M109"}:
            target = params.get("S", params.get("R", 0.0))
            max_nozzle = max(max_nozzle, target)
            if target > profile.max_nozzle_temp:
                issues.append(GCodeIssue(
                    "ERROR", lineno,
                    f"nozzle target {target:g}C exceeds the verified maximum {profile.max_nozzle_temp}C",
                    cmd,
                ))
            continue

        if cmd in {"M140", "M190"}:
            target = params.get("S", params.get("R", 0.0))
            max_bed = max(max_bed, target)
            if target > profile.max_bed_temp:
                issues.append(GCodeIssue(
                    "ERROR", lineno,
                    f"bed target {target:g}C exceeds the verified maximum {profile.max_bed_temp}C",
                    cmd,
                ))
            continue

        if cmd in {"G0", "G1", "G2", "G3"}:
            previous_e = pos["E"]
            for axis in ("X", "Y", "Z"):
                if axis in params:
                    value = params[axis] * unit_scale
                    pos[axis] = value if abs_xyz else pos[axis] + value
            if "E" in params:
                value = params["E"] * unit_scale
                pos["E"] = value if abs_e else pos["E"] + value
            extruding = pos["E"] > previous_e + 1e-9
            if extruding:
                if cmd in {"G2", "G3"}:
                    # Endpoints are checked below, but the arc between them is
                    # not interpolated. Saying so is the honest option; silently
                    # passing an arc whose bulge leaves the bed is not.
                    issues.append(GCodeIssue(
                        "WARNING", lineno,
                        "extruding arc: only the endpoints are envelope-checked, "
                        "the arc between them is not interpolated by this scanner",
                        cmd,
                    ))
                if not homed:
                    issues.append(GCodeIssue(
                        "WARNING", lineno, "extrusion move before any G28 homing", cmd
                    ))
                for axis, limit in limits.items():
                    value = pos[axis]
                    if value < -1e-6 or value > limit + 1e-6:
                        issues.append(GCodeIssue(
                            "ERROR", lineno,
                            f"extrusion move {axis}={value:g} mm is outside the verified "
                            f"0..{limit:g} mm envelope",
                            cmd,
                        ))
                    ext_min[axis] = min(ext_min.get(axis, value), value)
                    ext_max[axis] = max(ext_max.get(axis, value), value)
            continue

        if strict_unknown and cmd not in KNOWN_COMMANDS and (cmd.startswith("G") or cmd.startswith("M")):
            issues.append(GCodeIssue("WARNING", lineno, "command not recognised by the scanner", cmd))
        elif not (cmd.startswith("G") or cmd.startswith("M") or cmd.startswith("T")):
            issues.append(GCodeIssue("WARNING", lineno, "line is not a G/M/T command", cmd))

    worst = max((SEVERITY_ORDER[i.severity] for i in issues), default=0)
    status = "FAILED" if worst == 2 else ("WARN" if worst == 1 else "PASS")
    return GCodeScan(
        status=status,
        issues=[asdict(i) for i in issues],
        lines_scanned=lines,
        commands_scanned=commands,
        max_nozzle_target_c=max_nozzle,
        max_bed_target_c=max_bed,
        extrusion_bounds_min_mm=ext_min,
        extrusion_bounds_max_mm=ext_max,
        profile_id=profile.id,
        units_mode=units_mode,
    )
