#!/usr/bin/env python3
"""Portable bootstrap / manifest validator for the local media engine (stable-diffusion.cpp).

Standard library only, so it runs on the owner's machine before the app is installed.
The schema of MANIFEST.json is documented in docs/media/SDCPP_ENGINE_RU.md and mirrored
by command-center/bcc/studio/providers/sdcpp.py (validate_manifest).

Subcommands (never download unless told so explicitly):
  validate <models_dir>                 per-file PRESENT_OK / PRESENT_BAD_HASH / MISSING / SIZE_MISMATCH
  plan-download <models_dir>            only missing/bad files with their manifest URLs, no network
  download <models_dir> --allow-download  fetch missing/bad files to .part, verify sha256, rename
  write-manifest <models_dir> ...       hash existing files into a manifest skeleton (owner review needed)
  configure --sdcpp-bin PATH --models-dir DIR   write <data_dir>/media/config.json (launches without env vars)

The models directory comes from the positional argument or BOSSMAN_MEDIA_MODELS; nothing here
hardcodes an owner path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath

MANIFEST_NAME = "MANIFEST.json"
SCHEMA_VERSION = 1
HEX64 = re.compile(r"^[0-9a-f]{64}$")
STATUS_OK = "PRESENT_OK"
STATUS_BAD_HASH = "PRESENT_BAD_HASH"
STATUS_MISSING = "MISSING"
STATUS_SIZE = "SIZE_MISMATCH"
ROLE_PATTERNS = (("vae", "vae"), ("ae.", "vae"), ("umt5", "text_encoder"), ("t5xxl", "text_encoder"), ("t5", "text_encoder"),
                 ("qwen", "text_encoder"), ("text_encoder", "text_encoder"), ("llm", "text_encoder"),
                 ("clip", "clip"), ("projector", "projector"))


# ----------------------------------------------------------------------------- manifest schema

def check_relative_path(label: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest {label}: path required")
    if "\x00" in value or "\\" in value:
        raise ValueError(f"manifest {label}: path must use forward slashes")
    posix = PurePosixPath(value)
    if posix.is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ValueError(f"manifest {label}: path must be relative")
    if not posix.parts or any(p in ("..", ".", "") for p in posix.parts):
        raise ValueError(f"manifest {label}: path escapes the model directory")
    return value


def validate_manifest(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("manifest: object required")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("manifest schema_version: must be 1")
    engine = data.get("engine")
    if not isinstance(engine, dict) or not isinstance(engine.get("release"), str) or not engine["release"]:
        raise ValueError("manifest engine.release: string required")
    if engine.get("binary_sha256") is not None and not (isinstance(engine["binary_sha256"], str)
                                                        and HEX64.match(engine["binary_sha256"])):
        raise ValueError("manifest engine.binary_sha256: 64 lowercase hex chars required")
    engines = data.get("engines")
    if not isinstance(engines, dict) or not engines:
        raise ValueError("manifest engines: non-empty object required")
    for name, entry in engines.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), dict) or not entry["files"]:
            raise ValueError(f"manifest engines.{name}: files object required")
        for role, spec in entry["files"].items():
            if not isinstance(spec, dict):
                raise ValueError(f"manifest {name}.{role}: object required")
            check_relative_path(f"{name}.{role}", spec.get("path"))
            if type(spec.get("bytes")) is not int or spec["bytes"] < 0:
                raise ValueError(f"manifest {name}.{role}: bytes must be a non-negative integer")
            if not isinstance(spec.get("sha256"), str) or not HEX64.match(spec["sha256"]):
                raise ValueError(f"manifest {name}.{role}: sha256 must be 64 lowercase hex chars")
            for opt in ("revision", "url"):
                if spec.get(opt) is not None and not isinstance(spec[opt], str):
                    raise ValueError(f"manifest {name}.{role}: {opt} must be a string")
    return data


def load_manifest(models_dir: Path) -> dict:
    path = Path(models_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"manifest: unreadable {path} ({type(exc).__name__})") from exc
    return validate_manifest(data)


def sha256_file(path: Path, progress=None) -> str:
    h = hashlib.sha256()
    done = 0
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return h.hexdigest()


def resolve_model_file(models_dir: Path, rel: str) -> Path:
    root = Path(models_dir).resolve()
    lexical = root.joinpath(*PurePosixPath(rel).parts)
    if lexical.is_symlink() or lexical.resolve() != lexical:
        raise PermissionError(f"{rel}: symlink/junction inside the models directory is not accepted")
    if root not in lexical.parents:
        raise PermissionError(f"{rel}: escapes the models directory")
    return lexical


# ----------------------------------------------------------------------------- validate

def validate_files(models_dir: Path, manifest: dict, *, hash_files: bool = True, log=None) -> list[dict]:
    """One row per manifest file. hash_files=False stops at size (a 'plan' needs no hashing of good files)."""
    rows = []
    for engine, entry in manifest["engines"].items():
        for role, spec in entry["files"].items():
            row = {"engine": engine, "role": role, "path": spec["path"], "bytes_expected": spec["bytes"],
                   "sha256_expected": spec["sha256"], "url": spec.get("url"), "revision": spec.get("revision"),
                   "bytes_observed": None, "sha256_observed": None, "status": None}
            try:
                path = resolve_model_file(models_dir, spec["path"])
            except PermissionError as exc:
                row["status"], row["detail"] = STATUS_MISSING, str(exc)
                rows.append(row)
                continue
            if not path.is_file():
                row["status"] = STATUS_MISSING
            else:
                row["bytes_observed"] = path.stat().st_size
                if row["bytes_observed"] != spec["bytes"]:
                    row["status"] = STATUS_SIZE
                elif hash_files:
                    if log:
                        log(f"hashing {spec['path']} ({spec['bytes']} bytes)")
                    row["sha256_observed"] = sha256_file(path)
                    row["status"] = STATUS_OK if row["sha256_observed"] == spec["sha256"] else STATUS_BAD_HASH
                else:
                    row["status"] = "SIZE_OK_NOT_HASHED"
            rows.append(row)
    return rows


def validate_binary(manifest: dict, binary: str | None) -> dict:
    expected = manifest.get("engine", {}).get("binary_sha256")
    if not binary:
        return {"status": "BINARY_NOT_GIVEN", "sha256_expected": expected, "sha256_observed": None}
    exe = Path(binary)
    if not exe.is_file():
        return {"status": "BINARY_MISSING", "path": str(exe), "sha256_expected": expected, "sha256_observed": None}
    observed = sha256_file(exe)
    if expected is None:
        status = "BINARY_UNPINNED"
    else:
        status = "BINARY_OK" if observed == expected else "BINARY_BAD_HASH"
    return {"status": status, "path": str(exe), "sha256_expected": expected, "sha256_observed": observed,
            "release_declared": manifest.get("engine", {}).get("release")}


def needs_download(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] in (STATUS_MISSING, STATUS_SIZE, STATUS_BAD_HASH)]


# ----------------------------------------------------------------------------- download

def fetch_url(url: str, dest_part: Path, *, expected_bytes: int, log=None, timeout: int = 60) -> int:
    """Stream url → dest_part. Returns bytes written. Separated so tests replace it."""
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError(f"download: unsupported url scheme for {url}")
    written = 0
    request = urllib.request.Request(url, headers={"User-Agent": "bossman-media-bootstrap/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response, dest_part.open("wb") as fh:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            written += len(chunk)
            if written > expected_bytes:
                raise ValueError(f"download: {url} exceeds the manifest size {expected_bytes}")
            if log and written % (64 << 20) < (1 << 20):
                log(f"  {written}/{expected_bytes} bytes")
    return written


def download_missing(models_dir: Path, manifest: dict, rows: list[dict], *, allow: bool, fetch=fetch_url,
                     log=None, only: set[str] | None = None) -> list[dict]:
    if not allow:
        raise PermissionError("download: refused without --allow-download (explicit opt-in)")
    report = []
    for row in needs_download(rows):
        key = f"{row['engine']}:{row['role']}"
        if only and key not in only:
            continue
        result = {**row, "download": None}
        if not row.get("url"):
            result["download"] = "NO_URL"
            report.append(result)
            continue
        dest = resolve_model_file(models_dir, row["path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            if log:
                log(f"downloading {key} -> {row['path']}")
            written = fetch(row["url"], part, expected_bytes=row["bytes_expected"], log=log)
            if written != row["bytes_expected"]:
                raise ValueError(f"size {written} != manifest {row['bytes_expected']}")
            observed = sha256_file(part)
            if observed != row["sha256_expected"]:
                raise ValueError(f"sha256 {observed[:12]}… != manifest {row['sha256_expected'][:12]}…")
            os.replace(part, dest)
            result.update({"download": "VERIFIED", "sha256_observed": observed, "bytes_observed": written,
                           "status": STATUS_OK})
        except (OSError, ValueError) as exc:
            part.unlink(missing_ok=True)
            result.update({"download": "FAILED", "detail": f"{type(exc).__name__}: {exc}"[:300]})
        report.append(result)
    return report


# ----------------------------------------------------------------------------- write-manifest

def guess_role(name: str) -> str:
    lower = name.lower()
    for needle, role in ROLE_PATTERNS:
        if needle in lower:
            return role
    return "diffusion"


def hash_tree(models_dir: Path, *, release: str, binary: str | None = None, files: list[str] | None = None,
              auto: bool = False, merge: dict | None = None, log=None) -> dict:
    """Manifest skeleton from real files. `files`: ENGINE:ROLE=relative/path entries. `auto`: one
    subdirectory = one engine, roles guessed from file names (marked for owner review)."""
    root = Path(models_dir).resolve()
    engines: dict[str, dict] = {}
    entries: list[tuple[str, str, str, bool]] = []
    for item in files or ():
        m = re.fullmatch(r"([A-Za-z0-9._-]+):([A-Za-z0-9_]+)=(.+)", item)
        if not m:
            raise ValueError(f"--file expects ENGINE:ROLE=relative/path, got {item!r}")
        entries.append((m.group(1), m.group(2), m.group(3), False))
    if auto:
        known = {}  # path -> (engine, role) from the manifest being merged: a known file keeps its role
        for engine, entry in ((merge or {}).get("engines") or {}).items():
            for role, spec in (entry.get("files") or {}).items() if isinstance(entry, dict) else ():
                if isinstance(spec, dict) and isinstance(spec.get("path"), str):
                    known[spec["path"]] = (engine, role)
        for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for f in sorted(p for p in sub.rglob("*") if p.is_file() and not p.name.endswith(".part")):
                rel = f.relative_to(root).as_posix()
                if any(rel == e[2] for e in entries):
                    continue
                if rel in known:
                    entries.append((known[rel][0], known[rel][1], rel, False))
                else:
                    entries.append((sub.name, guess_role(f.name), rel, True))
    if not entries:
        raise ValueError("write-manifest: nothing to hash (give --file ENGINE:ROLE=path or --auto)")
    review = []
    for engine, role, rel, guessed in entries:
        check_relative_path(f"{engine}.{role}", rel)
        path = resolve_model_file(root, rel)
        if not path.is_file():
            raise FileNotFoundError(f"{engine}:{role}: {rel} missing")
        files_map = engines.setdefault(engine, {"files": {}})["files"]
        if role in files_map:
            raise ValueError(f"{engine}: role {role} given twice ({files_map[role]['path']} and {rel})")
        size = path.stat().st_size
        if log:
            log(f"hashing {rel} ({size} bytes)")
        spec = {"path": rel, "bytes": size, "sha256": sha256_file(path)}
        old = (merge or {}).get("engines", {}).get(engine, {}).get("files", {}).get(role)
        if isinstance(old, dict):
            for opt in ("revision", "url"):
                if isinstance(old.get(opt), str):
                    spec[opt] = old[opt]
        files_map[role] = spec
        if guessed:
            review.append(f"{engine}:{role} <- {rel} (role guessed from the file name)")
    engine_block = {"release": release}
    if merge and isinstance(merge.get("engine"), dict):
        for k, v in merge["engine"].items():
            if k not in ("release", "binary_sha256"):
                engine_block[k] = v
    if binary:
        exe = Path(binary)
        if not exe.is_file():
            raise FileNotFoundError(f"engine binary missing: {exe}")
        engine_block["binary_sha256"] = sha256_file(exe)
    manifest = {"schema_version": SCHEMA_VERSION, "engine": engine_block, "engines": engines,
                "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": "tools/media_bootstrap.py"}
    if review:
        manifest["review_needed"] = review
    return validate_manifest(manifest)


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- configure

def _repo_command_center() -> Path:
    return Path(__file__).resolve().parents[1] / "command-center"


def default_data_dir() -> Path | None:
    """bcc.config semantics (BCC_DATA_DIR, else the platform data dir) when the package is importable."""
    try:
        cc = _repo_command_center()
        if cc.is_dir() and str(cc) not in sys.path:
            sys.path.insert(0, str(cc))
        from bcc.config import _data_dir
        return _data_dir()
    except Exception:
        configured = os.environ.get("BCC_DATA_DIR", "").strip()
        return Path(configured).expanduser() if configured else None


def configure(binary: str, models_dir: str, data_dir: str | None = None) -> Path:
    try:
        cc = _repo_command_center()
        if cc.is_dir() and str(cc) not in sys.path:
            sys.path.insert(0, str(cc))
        from bcc.studio.providers.sdcpp import write_configuration
        return write_configuration(binary, models_dir, data_dir)
    except ImportError:
        pass
    # Standalone fallback: same file, same validations.
    exe, models = Path(binary).expanduser(), Path(models_dir).expanduser()
    if not exe.is_file():
        raise FileNotFoundError(f"engine binary missing: {exe}")
    load_manifest(models)
    base = Path(data_dir) if data_dir else default_data_dir()
    if base is None:
        raise ValueError("configure: give --data-dir or set BCC_DATA_DIR (bcc package not importable)")
    path = base / "media" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"schema_version": 1, "sdcpp_bin": str(exe.resolve()), "models_dir": str(models.resolve()),
                             "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return path


# ----------------------------------------------------------------------------- CLI


def utf8_console() -> None:
    """Печать не имеет права падать на кириллице: под `python -I` PYTHONUTF8 игнорируется,
    поток получает кодировку локали Windows; errors='replace' держит печать живой везде."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _models_dir(arg: str | None) -> Path:
    value = arg or os.environ.get("BOSSMAN_MEDIA_MODELS", "").strip()
    if not value:
        raise SystemExit("models directory required: positional argument or BOSSMAN_MEDIA_MODELS")
    return Path(value).expanduser()


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _print(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    if isinstance(data, dict) and "files" in data:
        for row in data["files"]:
            print(f"{row['status']:<20} {row['engine']}:{row['role']:<14} {row['path']}")
        if data.get("binary"):
            print(f"{data['binary']['status']:<20} engine binary")
        print(f"verdict: {data['verdict']}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "plan-download", "download"):
        p = sub.add_parser(name)
        p.add_argument("models_dir", nargs="?")
        p.add_argument("--json", action="store_true")
        p.add_argument("--bin", default=os.environ.get("BOSSMAN_SDCPP_BIN") or None)
        if name == "validate":
            p.add_argument("--out", default=None, help="write the JSON report ({verdict, files:[{path,status}]}) here")
        if name == "download":
            p.add_argument("--allow-download", action="store_true", help="explicit opt-in; nothing is fetched without it")
            p.add_argument("--only", action="append", default=[], help="ENGINE:ROLE to fetch (repeatable)")
    p = sub.add_parser("write-manifest")
    p.add_argument("models_dir", nargs="?")
    p.add_argument("--release", required=True, help="engine release string, e.g. master-890-74988b2")
    p.add_argument("--bin", default=os.environ.get("BOSSMAN_SDCPP_BIN") or None)
    p.add_argument("--file", action="append", default=[], help="ENGINE:ROLE=relative/path (repeatable)")
    p.add_argument("--auto", action="store_true", help="subdir = engine, roles guessed from names (review!)")
    p.add_argument("--merge", action="store_true", help="keep url/revision from the existing MANIFEST.json")
    p.add_argument("--out", default=None, help="output path (default: <models_dir>/MANIFEST.json)")
    p.add_argument("--force", action="store_true", help="overwrite an existing output")
    p = sub.add_parser("configure")
    p.add_argument("--sdcpp-bin", "--bin", dest="bin", required=True, help="path to sd-cli(.exe)")
    p.add_argument("--models-dir", "--models", dest="models", required=True, help="directory with MANIFEST.json")
    p.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    if args.command in ("validate", "plan-download", "download"):
        models = _models_dir(args.models_dir)
        try:
            manifest = load_manifest(models)
        except ValueError as exc:
            report = {"verdict": "INVALID", "reason": "MANIFEST_INVALID", "error": str(exc), "files": []}
            if getattr(args, "out", None):
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(Path(args.out), report)
            _print(report, args.json)
            return 2
        if args.command == "validate":
            rows = validate_files(models, manifest, log=_log)
            binary = validate_binary(manifest, args.bin)
            bad = needs_download(rows) or binary["status"] in ("BINARY_MISSING", "BINARY_BAD_HASH")
            report = {"verdict": "INVALID" if bad else "OK", "models_dir": str(models), "files": rows, "binary": binary,
                      "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(out, report)
            _print(report, args.json)
            return 1 if bad else 0
        rows = validate_files(models, manifest, log=_log)
        if args.command == "plan-download":
            plan = [{"engine": r["engine"], "role": r["role"], "path": r["path"], "status": r["status"],
                     "bytes": r["bytes_expected"], "sha256": r["sha256_expected"], "url": r["url"] or "NO_URL"}
                    for r in needs_download(rows)]
            _print({"plan": plan, "total_bytes": sum(p["bytes"] for p in plan), "downloads_performed": 0}, True)
            return 0
        try:
            report = download_missing(models, manifest, rows, allow=args.allow_download, log=_log,
                                      only=set(args.only) or None)
        except PermissionError as exc:
            _print({"verdict": "REFUSED", "error": str(exc)}, True)
            return 3
        failed = [r for r in report if r["download"] != "VERIFIED"]
        _print({"downloads": report, "verdict": "FAIL" if failed else "PASS"}, True)
        return 1 if failed else 0
    if args.command == "write-manifest":
        models = _models_dir(args.models_dir)
        out = Path(args.out) if args.out else models / MANIFEST_NAME
        if out.exists() and not args.force:
            _print({"verdict": "REFUSED", "error": f"{out} exists; pass --force to overwrite"}, True)
            return 3
        merge = None
        if args.merge and (models / MANIFEST_NAME).is_file():
            try:
                merge = json.loads((models / MANIFEST_NAME).read_text(encoding="utf-8"))
            except ValueError:
                merge = None
        try:
            manifest = hash_tree(models, release=args.release, binary=args.bin, files=args.file, auto=args.auto,
                                 merge=merge, log=_log)
        except (ValueError, OSError) as exc:
            _print({"verdict": "FAIL", "error": str(exc)}, True)
            return 1
        write_json_atomic(out, manifest)
        _print({"verdict": "WRITTEN", "path": str(out), "files": sum(len(e["files"]) for e in manifest["engines"].values()),
                "review_needed": manifest.get("review_needed", [])}, True)
        return 0
    if args.command == "configure":
        try:
            path = configure(args.bin, args.models, args.data_dir)
        except (OSError, ValueError) as exc:
            _print({"verdict": "FAIL", "error": str(exc)}, True)
            return 1
        _print({"verdict": "WRITTEN", "path": str(path)}, True)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
