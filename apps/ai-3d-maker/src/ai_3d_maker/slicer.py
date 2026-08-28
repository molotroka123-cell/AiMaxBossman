"""Slicer adapters. Optional stage, kept strictly outside the CAD core.

Two real adapters are wired: CuraEngine (the engine ELEGOO Cura ships) and
PrusaSlicer CLI. Neither is bundled and no vendor definition file is
fabricated. If the binary or the machine definition is missing, slicing
reports NOT_AVAILABLE with the reason and the pipeline continues without a
G-code artifact.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SliceResult:
    status: str  # PASS | FAILED | NOT_AVAILABLE
    engine: str
    gcode_path: str | None = None
    returncode: int | None = None
    error: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    command: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "engine": self.engine,
            "ok": self.ok,
            "gcode_path": self.gcode_path,
            "returncode": self.returncode,
            "error": self.error,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "command": self.command,
        }


def curaengine_info(binary: str = "CuraEngine", definition: str = "") -> dict:
    path = shutil.which(binary)
    definition_ok = bool(definition) and Path(definition).is_file()
    reasons = []
    if path is None:
        reasons.append(f"{binary!r} not found on PATH")
    if not definition:
        reasons.append("no printer definition configured (AI3D_CURA_DEFINITION)")
    elif not definition_ok:
        reasons.append(f"printer definition {definition!r} does not exist")
    return {
        "name": "curaengine",
        "available": path is not None and definition_ok,
        "path": path,
        "definition": definition or None,
        "reason": "; ".join(reasons) or None,
    }


def prusaslicer_info(binary: str = "prusa-slicer") -> dict:
    path = shutil.which(binary)
    return {
        "name": "prusaslicer",
        "available": path is not None,
        "path": path,
        "reason": None if path else f"{binary!r} not found on PATH",
    }


async def _run(cmd: list[str], timeout_s: float) -> tuple[int | None, str, str, str | None]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        return None, "", "", str(exc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return None, "", "", f"slicer exceeded {timeout_s}s"
    return proc.returncode, out.decode(errors="replace")[-3000:], err.decode(errors="replace")[-3000:], None


async def slice_with_curaengine(
    stl_path: Path,
    gcode_path: Path,
    *,
    definition_path: str,
    binary: str = "CuraEngine",
    settings: dict | None = None,
    timeout_s: float = 300.0,
) -> SliceResult:
    info = curaengine_info(binary, definition_path)
    if not info["available"]:
        return SliceResult("NOT_AVAILABLE", "curaengine", error=info["reason"])
    cmd = [binary, "slice", "-v", "-j", definition_path, "-l", str(stl_path), "-o", str(gcode_path)]
    for key, value in (settings or {}).items():
        cmd += ["-s", f"{key}={value}"]
    rc, out, err, failure = await _run(cmd, timeout_s)
    if failure is not None:
        return SliceResult("FAILED", "curaengine", error=failure, command=cmd)
    ok = rc == 0 and gcode_path.is_file()
    return SliceResult(
        "PASS" if ok else "FAILED",
        "curaengine",
        gcode_path=str(gcode_path) if ok else None,
        returncode=rc,
        error=None if ok else f"CuraEngine returned {rc}",
        stdout_tail=out,
        stderr_tail=err,
        command=cmd,
    )


async def slice_with_prusaslicer(
    stl_path: Path,
    gcode_path: Path,
    *,
    binary: str = "prusa-slicer",
    config_path: str = "",
    extra_args: list[str] | None = None,
    timeout_s: float = 300.0,
) -> SliceResult:
    info = prusaslicer_info(binary)
    if not info["available"]:
        return SliceResult("NOT_AVAILABLE", "prusaslicer", error=info["reason"])
    cmd = [binary, "--export-gcode", "--output", str(gcode_path)]
    if config_path:
        cmd += ["--load", config_path]
    cmd += list(extra_args or [])
    cmd.append(str(stl_path))
    rc, out, err, failure = await _run(cmd, timeout_s)
    if failure is not None:
        return SliceResult("FAILED", "prusaslicer", error=failure, command=cmd)
    ok = rc == 0 and gcode_path.is_file()
    return SliceResult(
        "PASS" if ok else "FAILED",
        "prusaslicer",
        gcode_path=str(gcode_path) if ok else None,
        returncode=rc,
        error=None if ok else f"PrusaSlicer returned {rc}",
        stdout_tail=out,
        stderr_tail=err,
        command=cmd,
    )


async def slice_auto(
    stl_path: Path,
    gcode_path: Path,
    *,
    curaengine_bin: str = "CuraEngine",
    cura_definition: str = "",
    prusaslicer_bin: str = "prusa-slicer",
    settings: dict | None = None,
    timeout_s: float = 300.0,
) -> SliceResult:
    """Try the configured engines in order; report honestly when none is usable."""
    cura = curaengine_info(curaengine_bin, cura_definition)
    if cura["available"]:
        return await slice_with_curaengine(
            stl_path, gcode_path,
            definition_path=cura_definition, binary=curaengine_bin,
            settings=settings, timeout_s=timeout_s,
        )
    prusa = prusaslicer_info(prusaslicer_bin)
    if prusa["available"]:
        return await slice_with_prusaslicer(
            stl_path, gcode_path, binary=prusaslicer_bin, timeout_s=timeout_s
        )
    return SliceResult(
        "NOT_AVAILABLE",
        "none",
        error="no slicer available: " + "; ".join(
            filter(None, [cura["reason"], prusa["reason"]])
        ),
    )
