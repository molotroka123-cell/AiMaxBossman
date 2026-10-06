"""OrcaSlicer adapter: slice an STL with a real bundled Orca preset, then scan.

    python -m ai_3d_maker.orca locate
    python -m ai_3d_maker.orca profiles [--kind machine|process|filament] [--vendor V] [--printer NAME]
    python -m ai_3d_maker.orca slice MODEL.stl --printer NAME --process NAME --filament NAME --out DIR
    python -m ai_3d_maker.orca open MODEL.stl        # owner looks at it in the Orca GUI

The same verbs are reachable as `ai-3d-maker orca ...`.

What was measured against OrcaSlicer 2.4.2 (portable, Windows), not assumed:

* The CLI options are `--slice N` (0 = all plates), `--load-settings
  "machine.json;process.json"`, `--load-filaments "f1.json;..."`,
  `--outputdir DIR`, `--datadir DIR`, `--export-3mf FILE`, `--debug N`.
  There is no `--export-gcode` / `--output` (those are PrusaSlicer flags):
  Orca writes `plate_<N>.gcode` into `--outputdir`.
* The CLI does NOT resolve `inherits` in the bundled vendor presets. Loading a
  leaf preset directly fails validation (exit -51). Each preset is therefore
  flattened here (root -> leaf) before it is passed in.
* A flattened preset with `"from": "User"` and no `inherits` is rejected as
  "process not compatible" (exit -17). It is accepted with `"from": "system"`
  and `*_settings_id` set to the preset's own name.
* `orca-slicer.exe` is a GUI-subsystem binary: `--help` prints nothing to a
  redirected stdout, and errors are written to stderr only sometimes.

Safety: this module never talks to a printer and never opens a network
connection. Orca runs with a private `--datadir` per run so the owner's GUI
configuration is not read or modified. Physical printing stays where it was:
only `ControlPlane.printer_confirm` with a per-job confirmation token and
`AI3D_ALLOW_PHYSICAL_PRINT=1` can reach hardware, and nothing here calls it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

from .config import load_settings
from .errors import (
    Ai3dError,
    CapabilityUnavailableError,
    MeshLoadError,
    ProfileNotFoundError,
    SlicerFailedError,
)
from .gcode import scan_gcode
from .mesh import BINARY_TRI_SIZE, _looks_ascii_stl
from .profile import PrinterProfile

ORCA_ENV = "AI3D_ORCA_EXE"
KINDS = ("machine", "process", "filament")
SHARED_FILAMENT_VENDOR = "OrcaFilamentLibrary"
DEFAULT_TIMEOUT_S = 300.0
MAX_INHERIT_DEPTH = 20
_RUN_MARKER = ".bossman-orca-run"

# Orca CLI exit codes observed on this host. Anything else is reported raw.
OBSERVED_EXIT_CODES = {
    -17: "process preset is not compatible with the printer preset",
    -51: "configuration failed Orca's validation (see stderr)",
}


# ----------------------------------------------------------------- locating
def _candidates() -> list[Path]:
    out: list[Path] = []
    explicit = os.getenv(ORCA_ENV)
    if explicit:
        out.append(Path(explicit).expanduser())
    for name in ("orca-slicer", "orca-slicer.exe", "OrcaSlicer"):
        found = shutil.which(name)
        if found:
            out.append(Path(found))
    home = Path.home()
    out += [
        home / "Bossman" / "apps-local" / "OrcaSlicer" / "orca-slicer.exe",
        Path(os.getenv("ProgramFiles") or r"C:\Program Files") / "OrcaSlicer" / "orca-slicer.exe",
    ]
    return out


def locate_orca() -> Path | None:
    """First existing OrcaSlicer binary: $AI3D_ORCA_EXE, PATH, then known installs."""
    for path in _candidates():
        if path.is_file():
            return path.resolve()
    return None


def _require_orca(orca: str | Path | None) -> Path:
    exe = Path(orca) if orca else locate_orca()
    if exe is None or not exe.is_file():
        raise CapabilityUnavailableError(
            "OrcaSlicer not found (set AI3D_ORCA_EXE or install it)",
            detail={"searched": [str(p) for p in _candidates()]},
        )
    return exe


def profiles_root(orca: str | Path | None = None) -> Path:
    root = _require_orca(orca).parent / "resources" / "profiles"
    if not root.is_dir():
        raise CapabilityUnavailableError(f"Orca profiles directory missing: {root}")
    return root


# ------------------------------------------------------------ preset index
# Parsing all ~12k bundled preset files takes ~27 s on the owner PC (measured),
# so presets are indexed one vendor at a time and only when needed.
def _entry(vendor: str, path: Path, data: dict) -> dict:
    return {
        "vendor": vendor,
        "name": data.get("name"),
        "path": str(path),
        "inherits": data.get("inherits") or "",
        "instantiation": str(data.get("instantiation", "")).lower() == "true",
        "compatible_printers": data.get("compatible_printers"),
        "printer_model": data.get("printer_model"),
        "nozzle_diameter": data.get("nozzle_diameter"),
    }


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _vendors(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir())


@lru_cache(maxsize=64)
def _vendor_index(root_str: str, vendor: str) -> dict[tuple[str, str], dict]:
    """(kind, name) -> entry for one vendor directory."""
    index: dict[tuple[str, str], dict] = {}
    vendor_dir = Path(root_str) / vendor
    for kind in KINDS:
        kind_dir = vendor_dir / kind
        if not kind_dir.is_dir():
            continue
        for path in sorted(kind_dir.rglob("*.json")):
            data = _read_json(path)
            if data is None or not isinstance(data.get("name"), str):
                continue
            index.setdefault((kind, data["name"]), _entry(vendor, path, data))
    return index


def _lookup(root: Path, kind: str, name: str, vendor: str | None) -> dict | None:
    if vendor:
        for v in (vendor, SHARED_FILAMENT_VENDOR) if kind == "filament" else (vendor,):
            if (root / v).is_dir():
                hit = _vendor_index(str(root), v).get((kind, name))
                if hit:
                    return hit
        return None
    # No vendor: bundled leaf presets are stored as "<name>.json", so a file-name
    # search finds them without parsing every vendor's presets.
    hits = []
    for v in _vendors(root):
        kind_dir = root / v / kind
        if not kind_dir.is_dir():
            continue
        for path in kind_dir.rglob(f"{glob_escape(name)}.json"):
            data = _read_json(path)
            if data and data.get("name") == name:
                hits.append(_entry(v, path, data))
    if not hits:
        for v in _vendors(root):  # slow path: name differs from the file name
            hit = _vendor_index(str(root), v).get((kind, name))
            if hit:
                hits.append(hit)
    instantiable = [h for h in hits if h["instantiation"]] or hits
    if len({h["vendor"] for h in instantiable}) > 1:
        raise ProfileNotFoundError(
            f"{kind} preset {name!r} exists for several vendors; pass --vendor",
            detail={"vendors": sorted({h["vendor"] for h in instantiable})},
        )
    return instantiable[0] if instantiable else None


def glob_escape(text: str) -> str:
    return re.sub(r"([*?\[\]])", r"[\1]", text)


def _compatible_printers(root: Path, entry: dict, kind: str) -> list[str] | None:
    """compatible_printers of a preset, inherited from the nearest ancestor that sets it."""
    seen = 0
    while entry is not None and seen < MAX_INHERIT_DEPTH:
        if entry.get("compatible_printers"):
            return list(entry["compatible_printers"])
        if not entry["inherits"]:
            return None
        entry = _lookup(root, kind, entry["inherits"], entry["vendor"])
        seen += 1
    return None


def list_profiles(
    kind: str = "machine",
    *,
    vendor: str | None = None,
    printer: str | None = None,
    orca: str | Path | None = None,
) -> list[dict]:
    """User-selectable presets (instantiation=true) of one kind, optionally filtered.

    With `printer`, the vendor defaults to that printer's vendor and presets
    whose compatible_printers list excludes it are dropped. Without a vendor
    every vendor is parsed, which is slow (tens of seconds).
    """
    if kind not in KINDS:
        raise ProfileNotFoundError(f"unknown preset kind {kind!r}", detail={"kinds": list(KINDS)})
    root = profiles_root(orca)
    if printer and not vendor:
        machine = _lookup(root, "machine", printer, None)
        if machine is None:
            raise ProfileNotFoundError(f"machine preset {printer!r} not found")
        vendor = machine["vendor"]
    if vendor and not (root / vendor).is_dir():
        raise ProfileNotFoundError(f"vendor {vendor!r} not found", detail={"vendors": _vendors(root)})
    vendors = [vendor] if vendor else _vendors(root)
    if vendor and kind == "filament" and printer and vendor != SHARED_FILAMENT_VENDOR:
        vendors.append(SHARED_FILAMENT_VENDOR)
    out = []
    for v in vendors:
        for (k, name), e in _vendor_index(str(root), v).items():
            if k != kind or not e["instantiation"]:
                continue
            row = {"vendor": v, "name": name, "path": e["path"]}
            if kind == "machine":
                row["printer_model"] = e["printer_model"]
                row["nozzle_diameter"] = e["nozzle_diameter"]
            else:
                compat = _compatible_printers(root, e, kind)
                if printer and compat is not None and printer not in compat:
                    continue
                row["compatible_printers"] = compat
            out.append(row)
    return sorted(out, key=lambda r: (r["vendor"], r["name"]))


def list_printer_profiles(vendor: str | None = None, orca: str | Path | None = None) -> list[dict]:
    return list_profiles("machine", vendor=vendor, orca=orca)


def resolve_profile(
    kind: str,
    name_or_path: str,
    *,
    vendor: str | None = None,
    orca: str | Path | None = None,
) -> tuple[dict, dict]:
    """Flatten a preset (by bundled name or JSON path) into one self-contained dict.

    Returns (flattened_preset, provenance). The CLI cannot follow `inherits`,
    so the chain root -> leaf is merged here, leaf values winning.
    """
    root = profiles_root(orca)
    candidate = Path(name_or_path)
    if candidate.suffix.lower() == ".json" and candidate.is_file():
        leaf_data = json.loads(candidate.read_text(encoding="utf-8"))
        try:
            leaf_vendor = candidate.resolve().relative_to(root.resolve()).parts[0]
        except ValueError:
            leaf_vendor = vendor
        leaf = {"vendor": leaf_vendor, "path": str(candidate), "inherits": leaf_data.get("inherits") or ""}
    else:
        leaf = _lookup(root, kind, name_or_path, vendor)
        if leaf is None:
            raise ProfileNotFoundError(
                f"{kind} preset {name_or_path!r} not found in {root}"
                + (f" for vendor {vendor!r}" if vendor else ""),
                detail={"kind": kind, "name": name_or_path, "vendor": vendor},
            )
        leaf_data = json.loads(Path(leaf["path"]).read_text(encoding="utf-8"))
    if leaf_data.get("type") not in (None, kind):
        raise ProfileNotFoundError(
            f"{name_or_path!r} is a {leaf_data.get('type')!r} preset, not {kind!r}",
            detail={"kind": kind, "type": leaf_data.get("type")},
        )

    chain = [(leaf["path"], leaf_data)]
    parent, owner = leaf["inherits"], leaf["vendor"]
    while parent:
        if len(chain) > MAX_INHERIT_DEPTH:
            raise ProfileNotFoundError(f"{kind} preset {name_or_path!r}: inheritance deeper than {MAX_INHERIT_DEPTH}")
        entry = _lookup(root, kind, parent, owner)
        if entry is None:
            raise ProfileNotFoundError(
                f"{kind} preset {name_or_path!r}: parent {parent!r} not found (vendor {owner!r})",
                detail={"missing_parent": parent},
            )
        data = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
        chain.append((entry["path"], data))
        parent, owner = entry["inherits"], entry["vendor"]

    flat: dict = {}
    for _, data in reversed(chain):
        flat.update(data)
    name = leaf_data.get("name") or candidate.stem
    flat.pop("inherits", None)
    flat["name"] = name
    flat["from"] = "system"  # measured: "User" without inherits -> exit -17
    if kind == "machine":
        flat["printer_settings_id"] = name
    elif kind == "process":
        flat["print_settings_id"] = name
    else:
        flat["filament_settings_id"] = [name]
    provenance = {
        "name": name,
        "vendor": leaf["vendor"],
        "chain": [p for p, _ in chain],
    }
    return flat, provenance


# ------------------------------------------------------------- validation
def check_stl(stl_path: str | Path, *, max_bytes: int | None = None) -> dict:
    """Cheap STL sanity check (Orca parses it fully itself)."""
    path = Path(stl_path)
    if not path.exists():
        raise MeshLoadError(f"STL not found: {path}", detail={"path": str(path)})
    if not path.is_file():
        raise MeshLoadError(f"not a file: {path}", detail={"path": str(path)})
    if path.suffix.lower() != ".stl":
        raise MeshLoadError(f"{path.name}: expected a .stl file", detail={"suffix": path.suffix})
    size = path.stat().st_size
    limit = max_bytes if max_bytes is not None else load_settings().max_upload_bytes
    if size > limit:
        raise MeshLoadError(f"{path.name}: {size} bytes exceeds the {limit} byte limit",
                            detail={"bytes": size, "limit": limit})
    with path.open("rb") as fh:
        head = fh.read(4096)
    if len(head) == 0:
        raise MeshLoadError(f"{path.name} is empty")
    if _looks_ascii_stl(head):
        with path.open("rb") as fh:
            fh.seek(max(0, size - 1024))
            tail = fh.read().lower()
        if b"endsolid" not in tail:
            raise MeshLoadError(f"{path.name}: ASCII STL without 'endsolid' (truncated?)")
        return {"format": "ascii", "bytes": size}
    if size < 84:
        raise MeshLoadError(f"{path.name}: not an STL (binary STL needs >= 84 bytes, got {size})")
    (count,) = struct.unpack_from("<I", head, 80)
    if count == 0 or size != 84 + count * BINARY_TRI_SIZE:
        raise MeshLoadError(
            f"{path.name}: not a valid binary STL (header declares {count} triangles, "
            f"size {size} != {84 + count * BINARY_TRI_SIZE})",
            detail={"triangles": count, "bytes": size},
        )
    return {"format": "binary", "bytes": size, "triangles": count}


# ----------------------------------------------------------------- parsing
_DURATION = re.compile(r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")


def _duration_s(text: str) -> int | None:
    m = _DURATION.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _floats(text: str) -> list[float]:
    out = []
    for part in text.split(","):
        try:
            out.append(float(part.strip()))
        except ValueError:
            pass
    return out


def parse_gcode_summary(text: str) -> dict:
    """Header/footer facts Orca writes as comments. Missing facts stay None."""
    facts: dict = {
        "generator": None, "layers": None, "estimated_time": None, "estimated_time_s": None,
        "filament_used": {"mm": None, "cm3": None, "g": None}, "max_z_height": None,
        "printer_settings_id": None, "print_settings_id": None, "filament_settings_id": None,
    }
    for line in text.splitlines():
        if not line.startswith(";"):
            continue
        body = line[1:].strip()
        if body.startswith("generated by "):
            facts["generator"] = body[len("generated by "):].split(" on ")[0]
        elif body.startswith("total layer number:"):
            facts["layers"] = int(body.split(":", 1)[1].strip())
        elif body.startswith("total layers count =") and facts["layers"] is None:
            facts["layers"] = int(body.split("=", 1)[1].strip())
        elif body.startswith("max_z_height:"):
            facts["max_z_height"] = float(body.split(":", 1)[1].strip())
        elif body.startswith("estimated printing time (normal mode) ="):
            value = body.split("=", 1)[1].strip()
            facts["estimated_time"] = value
            facts["estimated_time_s"] = _duration_s(value)
        elif body.startswith("filament used ["):
            unit = body[len("filament used ["):].split("]", 1)[0]
            if unit in facts["filament_used"]:
                values = _floats(body.split("=", 1)[1])
                facts["filament_used"][unit] = values[0] if len(values) == 1 else values
        elif "=" in body:
            key, value = (s.strip() for s in body.split("=", 1))
            if key in ("printer_settings_id", "print_settings_id", "filament_settings_id"):
                facts[key] = value.strip('"')
    return facts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_summary(text: str) -> dict:
    settings = load_settings()
    profile = PrinterProfile.load(settings.printer_profile)
    scan = scan_gcode(text, profile, strict_unknown=settings.strict_gcode)
    counts: dict[str, int] = {}
    for issue in scan.issues:
        counts[issue["severity"]] = counts.get(issue["severity"], 0) + 1
    return {
        "status": scan.status,
        "profile_id": scan.profile_id,
        "printer_model": profile.model,
        "strict_unknown": settings.strict_gcode,
        "issue_counts": counts,
        "issues": scan.issues[:25],
        "issues_truncated": len(scan.issues) > 25,
        "max_nozzle_target_c": scan.max_nozzle_target_c,
        "max_bed_target_c": scan.max_bed_target_c,
        "extrusion_bounds_min_mm": scan.extrusion_bounds_min_mm,
        "extrusion_bounds_max_mm": scan.extrusion_bounds_max_mm,
        "lines_scanned": scan.lines_scanned,
    }


# ------------------------------------------------------------------- slice
def _prepare_run_dir(run_dir: Path) -> None:
    if run_dir.exists():
        if not (run_dir / _RUN_MARKER).is_file():
            raise SlicerFailedError(f"{run_dir} exists and was not created by this module; refusing to reuse it")
        shutil.rmtree(run_dir)
    (run_dir / "out").mkdir(parents=True)
    (run_dir / "datadir").mkdir()
    (run_dir / "profiles").mkdir()
    (run_dir / _RUN_MARKER).write_text("created by ai_3d_maker.orca\n", encoding="utf-8")


def _signed(returncode: int) -> int:
    # Windows reports a negative process exit code as unsigned 32-bit
    # (Orca's -17 arrives as 4294967279).
    return returncode - (1 << 32) if returncode >= (1 << 31) else returncode


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def slice(  # noqa: A001 - the name is the requested public API
    stl_path: str | Path,
    printer_profile: str,
    process_profile: str,
    filament_profile: str,
    out_dir: str | Path,
    *,
    vendor: str | None = None,
    orca: str | Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_stl_bytes: int | None = None,
) -> dict:
    """Slice one STL with Orca presets; return G-code facts plus the safety scan verdict.

    Raises a typed Ai3dError (code in `.code`) for every refusal; never returns
    a result without a G-code file.
    """
    stl = Path(stl_path).resolve()
    stl_info = check_stl(stl, max_bytes=max_stl_bytes)
    exe = _require_orca(orca)
    out = Path(out_dir).resolve()
    if exe.parent.resolve() in (out, *out.parents):
        raise SlicerFailedError(f"output directory {out} is inside the Orca install; choose another")
    if not (0 < timeout_s <= 3600):
        raise SlicerFailedError(f"timeout_s must be in (0, 3600], got {timeout_s}")

    machine, machine_src = resolve_profile("machine", printer_profile, vendor=vendor, orca=exe)
    owner_vendor = vendor or machine_src["vendor"]
    process, process_src = resolve_profile("process", process_profile, vendor=owner_vendor, orca=exe)
    filament, filament_src = resolve_profile("filament", filament_profile, vendor=owner_vendor, orca=exe)

    warnings: list[str] = []
    compat = process.get("compatible_printers") or []
    if compat and machine["name"] not in compat:
        warnings.append(f"process {process['name']!r} does not list printer {machine['name']!r} as compatible")
    fcompat = filament.get("compatible_printers") or []
    if fcompat and machine["name"] not in fcompat:
        warnings.append(f"filament {filament['name']!r} does not list printer {machine['name']!r} as compatible")

    out.mkdir(parents=True, exist_ok=True)
    run_dir = out / f"{stl.stem}.orca-run"
    _prepare_run_dir(run_dir)
    files = {}
    for key, data in (("machine", machine), ("process", process), ("filament", filament)):
        path = run_dir / "profiles" / f"{key}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        files[key] = path

    cmd = [
        str(exe),
        "--datadir", str(run_dir / "datadir"),
        "--load-settings", f"{files['machine']};{files['process']}",
        "--load-filaments", str(files["filament"]),
        "--slice", "0",
        "--outputdir", str(run_dir / "out"),
        str(stl),
    ]
    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell
            # cwd: on failure Orca writes "00000.log" into the working directory.
            cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout_s,
            creationflags=_no_window_flags(), check=False, cwd=str(run_dir),
        )
    except subprocess.TimeoutExpired as exc:
        raise SlicerFailedError(f"OrcaSlicer exceeded {timeout_s}s and was killed",
                                detail={"command": cmd}) from exc
    except OSError as exc:
        raise SlicerFailedError(f"cannot start OrcaSlicer: {exc}", detail={"command": cmd}) from exc
    wall_s = round(time.perf_counter() - started, 3)
    stdout = proc.stdout.decode(errors="replace")
    stderr = proc.stderr.decode(errors="replace")
    (run_dir / "orca.stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "orca.stderr.txt").write_text(stderr, encoding="utf-8")

    returncode = _signed(proc.returncode)
    produced = sorted((run_dir / "out").glob("plate_*.gcode"))
    if returncode != 0 or not produced:
        meaning = OBSERVED_EXIT_CODES.get(returncode)
        reason = (
            f"OrcaSlicer exit {returncode}" + (f" ({meaning})" if meaning else "")
            if returncode != 0 else "OrcaSlicer exited 0 but wrote no plate_*.gcode"
        )
        error_logs = {
            p.name: p.read_text(encoding="utf-8", errors="replace")[-2000:]
            for p in sorted(run_dir.glob("*.log"))
        }
        raise SlicerFailedError(reason, detail={
            "returncode": returncode, "orca_error_log": error_logs, "stderr_tail": stderr[-2000:],
            "stdout_tail": stdout[-2000:], "warnings": warnings,
            "command": cmd, "run_dir": str(run_dir),
        })
    if len(produced) > 1:
        warnings.append(f"Orca wrote {len(produced)} plates; only {produced[0].name} is reported")

    gcode_path = out / f"{stl.stem}.gcode"
    shutil.copyfile(produced[0], gcode_path)
    text = gcode_path.read_text(encoding="utf-8", errors="replace")
    summary = parse_gcode_summary(text)
    for key in ("layers", "estimated_time"):
        if summary[key] is None:
            warnings.append(f"G-code has no {key} comment; value unknown")
    if summary["filament_used"]["g"] is None:
        warnings.append("G-code has no 'filament used [g]' comment; value unknown")
    for line in (stderr + "\n" + stdout).splitlines():
        if "warn" in line.lower():
            warnings.append(f"orca: {line.strip()[:300]}")

    scan = _scan_summary(text)
    model = machine.get("printer_model") or ""
    if scan["printer_model"] and scan["printer_model"] not in model:
        warnings.append(
            f"safety scan used the app profile {scan['printer_model']!r} limits, "
            f"but the G-code was sliced for {model or machine['name']!r}"
        )

    result = {
        "status": "SLICED",
        "gcode_path": str(gcode_path),
        "gcode_bytes": gcode_path.stat().st_size,
        "sha256": _sha256(gcode_path),
        "estimated_time": summary["estimated_time"],
        "estimated_time_s": summary["estimated_time_s"],
        "filament_used": summary["filament_used"],
        "layers": summary["layers"],
        "max_z_height": summary["max_z_height"],
        "generator": summary["generator"],
        "warnings": warnings,
        "gcode_safety": scan,
        "slice_wall_s": wall_s,
        "stl": {"path": str(stl), **stl_info},
        "presets": {"machine": machine_src, "process": process_src, "filament": filament_src},
        "orca": {"path": str(exe), "returncode": returncode, "command": cmd, "run_dir": str(run_dir)},
        "printer_actions": "none (this module cannot send G-code or start a print)",
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# --------------------------------------------------------------------- GUI
def open_in_gui(stl_path: str | Path, *, orca: str | Path | None = None) -> dict:
    """Launch the Orca GUI with the model so the owner can look at it. Does not wait.

    Only the STL is passed; no print/send action is triggered. If an Orca
    window is already open, Orca may hand the file to that window instead.
    """
    stl = Path(stl_path).resolve()
    check_stl(stl)
    exe = _require_orca(orca)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
        [str(exe), str(stl)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
    )
    return {"status": "LAUNCHED", "pid": proc.pid, "command": [str(exe), str(stl)]}


# --------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ai_3d_maker.orca",
                                     description="OrcaSlicer adapter (slice + G-code safety scan; no printing)")
    parser.add_argument("--orca", default=None, help="path to orca-slicer.exe (default: auto / $AI3D_ORCA_EXE)")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("locate", help="show which Orca binary would be used")
    p = sub.add_parser("profiles", help="list bundled presets")
    p.add_argument("--kind", choices=KINDS, default="machine")
    p.add_argument("--vendor", default=None)
    p.add_argument("--printer", default=None, help="only presets compatible with this printer preset")
    p = sub.add_parser("slice", help="slice an STL and scan the G-code")
    p.add_argument("stl")
    p.add_argument("--printer", required=True, help="machine preset name or JSON path")
    p.add_argument("--process", required=True, help="process preset name or JSON path")
    p.add_argument("--filament", required=True, help="filament preset name or JSON path")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--vendor", default=None)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p = sub.add_parser("open", help="open an STL in the Orca GUI for the owner to look at")
    p.add_argument("stl")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verb == "locate":
            exe = Path(args.orca) if args.orca else locate_orca()
            payload = {"orca": str(exe) if exe and exe.is_file() else None,
                       "searched": [str(p) for p in _candidates()]}
            code = 0 if payload["orca"] else 2
        elif args.verb == "profiles":
            payload = list_profiles(args.kind, vendor=args.vendor, printer=args.printer, orca=args.orca)
            code = 0
        elif args.verb == "slice":
            payload = slice(args.stl, args.printer, args.process, args.filament, args.out,
                            vendor=args.vendor, orca=args.orca, timeout_s=args.timeout)
            code = 1 if payload["gcode_safety"]["status"] == "FAILED" else 0
        else:
            payload = open_in_gui(args.stl, orca=args.orca)
            code = 0
    except Ai3dError as exc:
        payload, code = exc.as_dict(), (2 if isinstance(exc, CapabilityUnavailableError) else 1)
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
