"""Slicer adapters. Optional stage, kept strictly outside the CAD core.

Two real adapters are wired: CuraEngine (the engine ELEGOO Cura ships) and
PrusaSlicer CLI. Neither is bundled and no vendor definition file is
fabricated. If the binary or the machine definition is missing, slicing
reports NOT_AVAILABLE with the reason and the pipeline continues without a
G-code artifact.

Three rules hold whatever engine is found:

* **A slicer that fails is never a PASS.** A zero exit code is not evidence
  either; the output file has to exist.
* **Whatever ran is identified.** Binary path, `--version` output and, for
  CuraEngine, the sha256 of the machine definition go into the result, so a
  G-code file can be traced back to the exact engine and profile that made it.
* **The command line is bounded.** Caller-supplied settings are validated
  against a narrow shape before any process is started; they can never become
  extra arguments.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .errors import InvalidSpecError

# A slicer setting key is a plain identifier — that is what both CuraEngine
# `-s key=value` and PrusaSlicer config keys look like. Anything that could be
# read as an option, a separator or a second argument is refused.
_SETTING_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MAX_SETTINGS = 200
_MAX_VALUE_CHARS = 200


def validate_slicer_settings(settings: dict | None) -> dict[str, str]:
    """Bound the caller-supplied part of the slicer command line.

    Values reach the slicer as single argv elements, so there is no shell to
    inject into — but an unchecked key or a value containing a newline still
    lets a caller reshape the invocation of a program that drives a heater.
    """
    settings = settings or {}
    if not isinstance(settings, dict):
        raise InvalidSpecError("slicer settings must be an object")
    if len(settings) > _MAX_SETTINGS:
        raise InvalidSpecError(
            f"{len(settings)} slicer settings exceeds the {_MAX_SETTINGS} limit",
            detail={"count": len(settings), "limit": _MAX_SETTINGS},
        )
    out: dict[str, str] = {}
    for key, value in settings.items():
        if not isinstance(key, str) or not _SETTING_KEY.match(key):
            raise InvalidSpecError(
                f"slicer setting name {key!r} is not a plain identifier",
                detail={"key": str(key)},
            )
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (int, float, str)):
            text = str(value)
        else:
            raise InvalidSpecError(
                f"slicer setting {key!r} must be a string, number or boolean",
                detail={"key": key, "type": type(value).__name__},
            )
        if len(text) > _MAX_VALUE_CHARS:
            raise InvalidSpecError(
                f"slicer setting {key!r} is longer than {_MAX_VALUE_CHARS} characters",
                detail={"key": key, "length": len(text)},
            )
        if any(c in text for c in "\n\r\0"):
            raise InvalidSpecError(
                f"slicer setting {key!r} contains a line break or NUL",
                detail={"key": key},
            )
        out[key] = text
    return out


# After a timeout the process is killed, but a slicer that forked children can
# keep the output pipe open. Reading it back is bounded too, otherwise the
# adapter would sit past its own deadline waiting for a process it already gave
# up on.
_DRAIN_AFTER_KILL_S = 2.0


async def _kill_and_drain(proc) -> None:
    try:
        proc.kill()
    except ProcessLookupError:  # pragma: no cover - already gone
        return
    try:
        # `wait()` and not `communicate()`: a forked grandchild can hold the
        # output pipe open long after the process we killed is gone, and
        # waiting on the pipe would reintroduce the hang we just escaped.
        await asyncio.wait_for(proc.wait(), _DRAIN_AFTER_KILL_S)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        # Release the pipes explicitly; otherwise they are only collected when
        # the event loop is already closing, which raises at interpreter exit.
        transport.close()


async def _probe_version(binary: str, args: list[str] | None = None, timeout_s: float = 15.0) -> str:
    """Ask the engine what it is. An engine that will not say stays 'unknown'."""
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *(args or ["--version"]),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return "unknown"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        await _kill_and_drain(proc)
        return "unknown"
    text = (out or b"").decode(errors="replace").strip() or (err or b"").decode(errors="replace").strip()
    return text.splitlines()[0][:200] if text else "unknown"


@dataclass(slots=True)
class SliceResult:
    status: str  # PASS | FAILED | NOT_AVAILABLE
    engine: str
    engine_version: str = "unknown"
    engine_path: str | None = None
    definition_sha256: str | None = None
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
            "engine_version": self.engine_version,
            "engine_path": self.engine_path,
            "definition_sha256": self.definition_sha256,
            "ok": self.ok,
            "gcode_path": self.gcode_path,
            "returncode": self.returncode,
            "error": self.error,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "command": self.command,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def curaengine_info(binary: str = "CuraEngine", definition: str = "") -> dict:
    """Probe CuraEngine and identify the machine definition it would use.

    The definition is the printer profile. It is never fabricated here: the
    operator points at the one their ELEGOO Cura install actually ships, and
    this records its digest so the G-code can be traced back to it.
    """
    path = shutil.which(binary)
    definition_path = Path(definition) if definition else None
    definition_ok = definition_path is not None and definition_path.is_file()
    reasons = []
    if path is None:
        reasons.append(f"{binary!r} not found on PATH")
    if not definition:
        reasons.append("no printer definition configured (AI3D_CURA_DEFINITION)")
    elif not definition_ok:
        reasons.append(f"printer definition {definition!r} does not exist")
    info = {
        "name": "curaengine",
        "available": path is not None and definition_ok,
        "path": path,
        "definition": definition or None,
        "definition_sha256": None,
        "definition_bytes": None,
        "reason": "; ".join(reasons) or None,
    }
    if definition_ok:
        info["definition_sha256"] = _sha256_file(definition_path)
        info["definition_bytes"] = definition_path.stat().st_size
    return info


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
        await _kill_and_drain(proc)
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
    try:
        checked = validate_slicer_settings(settings)
    except InvalidSpecError as exc:
        # Refused before a single process is started.
        return SliceResult(
            "FAILED", "curaengine", engine_path=info["path"],
            definition_sha256=info["definition_sha256"], error=str(exc),
        )
    cmd = [binary, "slice", "-v", "-j", definition_path, "-l", str(stl_path), "-o", str(gcode_path)]
    for key, value in checked.items():
        cmd += ["-s", f"{key}={value}"]
    version = await _probe_version(binary, ["--help"], timeout_s=min(timeout_s, 15.0))
    rc, out, err, failure = await _run(cmd, timeout_s)
    common = {
        "engine_version": version,
        "engine_path": info["path"],
        "definition_sha256": info["definition_sha256"],
        "command": cmd,
    }
    if failure is not None:
        return SliceResult("FAILED", "curaengine", error=failure, **common)
    ok = rc == 0 and gcode_path.is_file()
    return SliceResult(
        "PASS" if ok else "FAILED",
        "curaengine",
        gcode_path=str(gcode_path) if ok else None,
        returncode=rc,
        error=None if ok else (
            f"CuraEngine returned {rc}" if rc != 0
            else "CuraEngine returned 0 but wrote no G-code file"
        ),
        stdout_tail=out,
        stderr_tail=err,
        **common,
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
    version = await _probe_version(binary, timeout_s=min(timeout_s, 15.0))
    rc, out, err, failure = await _run(cmd, timeout_s)
    common = {"engine_version": version, "engine_path": info["path"], "command": cmd}
    if failure is not None:
        return SliceResult("FAILED", "prusaslicer", error=failure, **common)
    ok = rc == 0 and gcode_path.is_file()
    return SliceResult(
        "PASS" if ok else "FAILED",
        "prusaslicer",
        gcode_path=str(gcode_path) if ok else None,
        returncode=rc,
        error=None if ok else (
            f"PrusaSlicer returned {rc}" if rc != 0
            else "PrusaSlicer returned 0 but wrote no G-code file"
        ),
        stdout_tail=out,
        stderr_tail=err,
        **common,
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
