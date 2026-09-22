#!/usr/bin/env python3
"""Verifiable local-model profiles: plan / fetch / verify / pin / runtime-check.

Standard library only (Python 3.11+). Runs from the Windows bundle as ``app-support/model_fetch.py``
and from a checkout; it never depends on checkout paths, PYTHONPATH or an owner's user name.
The manifest is ``tools/model_profiles.json`` (schema documented in docs/owner/MODEL_PROFILES.md).

Actions (nothing is downloaded unless the action is ``fetch``):
  validate        manifest schema check (and the studio catalog contract when found)
  plan            per file: present / valid / missing / size-mismatch / partial / reusable,
                  bytes to download, free disk, fits?  No network.
  fetch           reuse -> resume -> download -> sha256 -> atomic rename.  Refuses UNPINNED
                  files unless --allow-unpinned (then the result is UNVERIFIED_TOFU, never verified).
  verify          sha256 of every present file against the manifest (hash cache: path+size+mtime).
  pin             read the HuggingFace API (HF_ENDPOINT or --hf-endpoint) and write a pinned copy
                  of the manifest to --out: commit revision, sizes and LFS sha256 per file.
  runtime-check   is the runtime binary of a profile present and is its version the pinned one?
                  Never installs anything, never touches drivers, BIOS or system Python.

Exit codes: 0 OK (everything required verified) · 1 FAILED (a required file failed) ·
2 INVALID (manifest / arguments) · 3 REFUSED (disk, unpinned, stale STOP file) · 4 CANCELLED ·
5 UNVERIFIED (no failure, but some required files are UNPINNED / TOFU only).
An optional profile's failure never changes the exit code of the required ones.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA_VERSION = 1
KIND = "bossman.model_profiles"
CATEGORIES = ("llm_coding_agents", "photo", "video", "tools_structured")
PROFILE_KINDS = ("model", "runtime", "reference")
PROFILE_STATUSES = ("PINNED", "UNPINNED", "PARTIALLY_PINNED", "REFERENCE")
FILE_STATUSES = ("PINNED", "UNPINNED")
REQUIRED_PROFILE_FIELDS = ("id", "category", "role", "runtime", "source", "files", "license", "min_free_disk",
                           "optional", "status")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX_PREFIX = re.compile(r"^[0-9a-f]{4,63}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
STUDIO_SURFACE = {"photo": "image", "video": "video"}

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_REFUSED, EXIT_CANCELLED, EXIT_UNVERIFIED = 0, 1, 2, 3, 4, 5

# per-file result states
REUSED, DOWNLOADED, RESUMED = "REUSED", "DOWNLOADED", "RESUMED"
FAILED_HASH, FAILED_SIZE, FAILED_NETWORK = "FAILED_HASH", "FAILED_SIZE", "FAILED_NETWORK"
CANCELLED, NOT_STARTED = "CANCELLED", "NOT_STARTED"
SKIPPED_OPTIONAL_FAILURE, UNPINNED_REFUSED = "SKIPPED_OPTIONAL_FAILURE", "UNPINNED_REFUSED"
UNVERIFIED_TOFU, VERIFIED, MISSING = "UNVERIFIED_TOFU", "VERIFIED", "MISSING"
DISK_INSUFFICIENT = "DISK_INSUFFICIENT"
FAILURE_STATES = {FAILED_HASH, FAILED_SIZE, FAILED_NETWORK, UNPINNED_REFUSED, MISSING, DISK_INSUFFICIENT}
OK_STATES = {REUSED, DOWNLOADED, RESUMED, VERIFIED}

STATE_DIR = ".model_fetch"
STOP_NAME = "STOP-model-fetch"
DEFAULT_MARGIN = 10 * 1024 ** 3
DEFAULT_CHUNK = 1 << 20
USER_AGENT = "bossman-model-fetch/1"


# ----------------------------------------------------------------------------- shared helpers
# The sha256 / relative-path code of tools/media_bootstrap.py is reused when it sits next to this
# file (checkout: tools/, bundle: app-support/).  Loaded by file path, so `python -I` works too.

def _load_sibling_media_bootstrap():
    path = Path(__file__).resolve().with_name("media_bootstrap.py")
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_bossman_media_bootstrap", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except Exception:  # noqa: BLE001 — a broken sibling must not break model fetching
        return None


_MEDIA = _load_sibling_media_bootstrap()


def _sha256_file_fallback(path: Path, progress=None) -> str:
    h = hashlib.sha256()
    done = 0
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return h.hexdigest()


def _check_relative_path_fallback(label: str, value) -> str:
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


def sha256_file(path: Path) -> str:
    if _MEDIA is not None and hasattr(_MEDIA, "sha256_file"):
        return _MEDIA.sha256_file(path)
    return _sha256_file_fallback(path)


check_relative_path = getattr(_MEDIA, "check_relative_path", None) or _check_relative_path_fallback


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return default


# ----------------------------------------------------------------------------- manifest schema

def _is_int(value) -> bool:
    return type(value) is int and value >= 0


def _file_rel(spec: dict) -> str | None:
    return f"{spec['dest_dir']}/{spec['name']}" if spec.get("name") else None


def validate_manifest(data, studio_catalog: dict | None = None) -> dict:
    """Raise ValueError listing every violation; return data when valid."""
    errors: list[str] = []
    err = errors.append
    if not isinstance(data, dict):
        raise ValueError("manifest: object required")
    if data.get("schema_version") != SCHEMA_VERSION:
        err("schema_version must be 1")
    if data.get("kind") != KIND:
        err(f"kind must be {KIND!r}")
    runtimes = data.get("runtimes")
    if not isinstance(runtimes, dict) or not runtimes:
        err("runtimes: non-empty object required")
        runtimes = {}
    for rid, rt in runtimes.items():
        if not isinstance(rt, dict):
            err(f"runtimes.{rid}: object required")
            continue
        for key in ("engine", "version", "status", "compatibility_notes", "license"):
            if key not in rt:
                err(f"runtimes.{rid}: {key} required")
        if rt.get("status") not in ("PINNED", "UNPINNED"):
            err(f"runtimes.{rid}.status: PINNED or UNPINNED")
        if rt.get("binary_sha256") is not None and not HEX64.match(str(rt["binary_sha256"])):
            err(f"runtimes.{rid}.binary_sha256: 64 lowercase hex or null")
        if rt.get("status") == "UNPINNED" and not rt.get("status_reason"):
            err(f"runtimes.{rid}: UNPINNED needs status_reason")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("; ".join(errors + ["profiles: non-empty list required"]))
    ids: set[str] = set()
    file_ids: dict[str, str] = {}
    dest_paths: dict[str, str] = {}
    globs: list[tuple[str, str, str | None]] = []  # (file id, glob, dest_dir)
    names: list[tuple[str, str, str]] = []  # (file id, name, dest_dir)
    for index, prof in enumerate(profiles):
        label = f"profiles[{index}]"
        if not isinstance(prof, dict):
            err(f"{label}: object required")
            continue
        missing = [k for k in REQUIRED_PROFILE_FIELDS if k not in prof]
        if missing:
            err(f"{label}: missing {', '.join(missing)}")
            continue
        pid = prof["id"]
        label = f"profile {pid}"
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", pid):
            err(f"{label}: id must be lowercase [a-z0-9._-]")
        if pid in ids:
            err(f"{label}: duplicate id")
        ids.add(pid)
        if prof["category"] not in CATEGORIES:
            err(f"{label}: category must be one of {CATEGORIES}")
        kind = prof.get("kind", "model")
        if kind not in PROFILE_KINDS:
            err(f"{label}: kind must be one of {PROFILE_KINDS}")
        if not isinstance(prof["role"], str) or not prof["role"].strip():
            err(f"{label}: role must be a non-empty string")
        if type(prof["optional"]) is not bool:
            err(f"{label}: optional must be true/false")
        if "reuse_only" in prof and type(prof["reuse_only"]) is not bool:
            err(f"{label}: reuse_only must be true/false")
        status = prof["status"]
        if status not in PROFILE_STATUSES:
            err(f"{label}: status must be one of {PROFILE_STATUSES}")
        rt = prof["runtime"]
        if not isinstance(rt, dict) or not isinstance(rt.get("ref"), str):
            err(f"{label}: runtime.ref required")
        elif rt["ref"] not in runtimes:
            err(f"{label}: runtime.ref {rt['ref']!r} is not in runtimes")
        src = prof["source"]
        if not isinstance(src, dict) or "revision" not in src or not any(k in src for k in ("hf_repo", "github_repo", "catalog")):
            err(f"{label}: source needs revision and one of hf_repo/github_repo/catalog")
            src = {}
        lic = prof["license"]
        if not isinstance(lic, dict) or not all(k in lic for k in ("id", "url", "acceptance_required")):
            err(f"{label}: license needs id, url, acceptance_required")
        elif not isinstance(lic["acceptance_required"], (bool, str)):
            err(f"{label}: license.acceptance_required must be bool or a description")
        mfd = prof["min_free_disk"]
        if not isinstance(mfd, dict) or "bytes" not in mfd or not isinstance(mfd.get("basis"), str):
            err(f"{label}: min_free_disk needs bytes and basis")
        elif mfd["bytes"] is None:
            if status not in ("UNPINNED", "REFERENCE"):
                err(f"{label}: min_free_disk.bytes may be null only for UNPINNED/REFERENCE profiles")
        elif not _is_int(mfd["bytes"]):
            err(f"{label}: min_free_disk.bytes must be a non-negative integer or null")
        files = prof["files"]
        if not isinstance(files, list):
            err(f"{label}: files must be a list")
            continue
        if status in ("UNPINNED", "PARTIALLY_PINNED", "REFERENCE") and not prof.get("status_reason"):
            err(f"{label}: status {status} needs status_reason")
        if status == "REFERENCE":
            if files:
                err(f"{label}: REFERENCE profiles carry no files (they point at another catalog)")
            if not (prof.get("studio_refs") or prof.get("model_refs")):
                err(f"{label}: REFERENCE needs studio_refs or model_refs")
        if kind == "model" and status != "REFERENCE" and not files:
            err(f"{label}: a model profile needs files")
        file_statuses = set()
        for findex, spec in enumerate(files):
            flabel = f"{label} files[{findex}]"
            if not isinstance(spec, dict):
                err(f"{flabel}: object required")
                continue
            for key in ("id", "dest_dir", "size_bytes", "sha256", "status"):
                if key not in spec:
                    err(f"{flabel}: {key} required")
            if "name" not in spec:
                err(f"{flabel}: name required (null only with name_glob)")
            fid = spec.get("id")
            if not isinstance(fid, str) or not fid:
                err(f"{flabel}: id required")
                continue
            flabel = f"{label} file {fid}"
            if fid in file_ids:
                err(f"{flabel}: duplicate file id (also in {file_ids[fid]})")
            file_ids[fid] = pid
            try:
                check_relative_path(flabel + ".dest_dir", spec.get("dest_dir"))
                if spec.get("name") is not None:
                    check_relative_path(flabel + ".name", spec["name"])
                    if "/" in spec["name"]:
                        raise ValueError(f"manifest {flabel}.name: a bare file name is required")
            except ValueError as exc:
                err(str(exc))
            fstatus = spec.get("status")
            file_statuses.add(fstatus)
            if fstatus not in FILE_STATUSES:
                err(f"{flabel}: status must be PINNED or UNPINNED")
            if spec.get("sha256_prefix") is not None and not HEX_PREFIX.match(str(spec["sha256_prefix"])):
                err(f"{flabel}: sha256_prefix must be 4..63 lowercase hex")
            if fstatus == "PINNED":
                if not isinstance(spec.get("name"), str):
                    err(f"{flabel}: PINNED needs an exact name")
                if not _is_int(spec.get("size_bytes")):
                    err(f"{flabel}: PINNED needs size_bytes (integer)")
                if not isinstance(spec.get("sha256"), str) or not HEX64.match(spec["sha256"]):
                    err(f"{flabel}: PINNED needs sha256 (64 lowercase hex)")
                elif spec.get("sha256_prefix") and not spec["sha256"].startswith(spec["sha256_prefix"]):
                    err(f"{flabel}: sha256 contradicts the observed sha256_prefix")
                if not (spec.get("url") or (src.get("hf_repo") and COMMIT.match(str(src.get("revision") or "")))):
                    err(f"{flabel}: PINNED needs url or source.hf_repo with a 40-hex commit revision")
            elif fstatus == "UNPINNED":
                if spec.get("sha256") is not None:
                    err(f"{flabel}: UNPINNED must have sha256 null (never a guessed hash)")
                if not spec.get("unpinned_reason"):
                    err(f"{flabel}: UNPINNED needs unpinned_reason")
                if spec.get("name") is None and not spec.get("name_glob"):
                    err(f"{flabel}: name or name_glob required")
            if spec.get("size_bytes") is not None and not _is_int(spec["size_bytes"]):
                err(f"{flabel}: size_bytes must be a non-negative integer or null")
            rel = _file_rel(spec) if isinstance(spec.get("name"), str) else None
            if rel:
                if rel.lower() in dest_paths:
                    err(f"{flabel}: destination {rel} already used by {dest_paths[rel.lower()]}")
                dest_paths[rel.lower()] = fid
                names.append((fid, spec["name"], spec.get("dest_dir")))
            if spec.get("name_glob"):
                globs.append((fid, spec["name_glob"], spec.get("dest_dir")))
        if status == "PINNED" and (not files or file_statuses != {"PINNED"}):
            err(f"{label}: PINNED needs every file PINNED")
        if status == "UNPINNED" and files and "UNPINNED" not in file_statuses:
            err(f"{label}: UNPINNED but every file is PINNED (status should be PINNED)")
        if status == "PARTIALLY_PINNED" and file_statuses != {"PINNED", "UNPINNED"}:
            err(f"{label}: PARTIALLY_PINNED needs both pinned and unpinned files")
    # distinct entries never share files: one file's name must not match another entry's glob
    for gid, glob, _gdir in globs:
        for nid, name, _ndir in names:
            if nid != gid and fnmatch.fnmatchcase(name, glob):
                err(f"file {nid} ({name}) matches the name_glob of file {gid} ({glob}) — entries would mix")
        for oid, other, _odir in globs:
            if oid != gid and (fnmatch.fnmatchcase(other, glob) or other == glob):
                err(f"file {oid} name_glob {other} overlaps file {gid} name_glob {glob}")
    for prof in profiles:
        if not isinstance(prof, dict):
            continue
        for ref in prof.get("model_refs") or []:
            if ref not in file_ids:
                err(f"profile {prof.get('id')}: model_refs {ref!r} is not a file id")
        if studio_catalog is not None:
            errors.extend(check_studio_refs(prof, studio_catalog))
    if errors:
        raise ValueError("; ".join(errors))
    return data


def check_studio_refs(prof: dict, catalog: dict) -> list[str]:
    """A photo/video profile references tools/studio_models.json ids; it never duplicates them."""
    errors = []
    by_id = {m.get("id"): m for m in catalog.get("models") or [] if isinstance(m, dict)}
    for ref in prof.get("studio_refs") or []:
        model = by_id.get(ref)
        if model is None:
            errors.append(f"profile {prof.get('id')}: studio_refs {ref!r} not in studio catalog")
            continue
        surface = STUDIO_SURFACE.get(prof.get("category"))
        if surface and model.get("surface") != surface:
            errors.append(f"profile {prof.get('id')}: {ref} is surface {model.get('surface')!r}, category needs {surface!r}")
        for key in ("provider", "license", "status", "source"):
            if not model.get(key):
                errors.append(f"profile {prof.get('id')}: studio model {ref} lacks {key}")
    return errors


def find_studio_catalog(explicit: str | None) -> dict | None:
    candidates = [Path(explicit)] if explicit else [Path(__file__).resolve().with_name("studio_models.json")]
    for path in candidates:
        if path.is_file():
            data = _read_json(path, None)
            if isinstance(data, dict):
                return data
            if explicit:
                raise ValueError(f"studio catalog unreadable: {path}")
    if explicit:
        raise ValueError(f"studio catalog not found: {explicit}")
    return None


def load_manifest(path: Path, studio_catalog: dict | None = None) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"manifest unreadable: {path} ({type(exc).__name__})") from exc
    return validate_manifest(data, studio_catalog)


def select_profiles(manifest: dict, wanted: list[str]) -> list[dict]:
    by_id = {p["id"]: p for p in manifest["profiles"]}
    if not wanted:
        return [p for p in manifest["profiles"] if not p["optional"]]
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        raise ValueError(f"unknown profile(s): {', '.join(unknown)}")
    return [by_id[w] for w in dict.fromkeys(wanted)]


# ----------------------------------------------------------------------------- local state

def default_models_dir() -> Path:
    env = os.environ.get("BOSSMAN_MODELS_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "Bossman" / "models"
    return Path.home() / ".local" / "share" / "Bossman" / "models"


class State:
    """Hash cache (path+size+mtime_ns), reference map and TOFU lock under <models>/.model_fetch/."""

    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.dir = self.models_dir / STATE_DIR
        self.cache_path = self.dir / "hashcache.json"
        self.refs_path = self.dir / "refs.json"
        self.tofu_path = self.dir / "tofu-lock.json"
        self.cache = _read_json(self.cache_path, {})
        self.refs = _read_json(self.refs_path, {})
        self.tofu = _read_json(self.tofu_path, {})
        self.hashed_now: list[str] = []

    def sha256(self, path: Path, log=None) -> str:
        path = Path(path)
        st = path.stat()
        key = os.path.normcase(str(path.resolve()))
        hit = self.cache.get(key)
        if isinstance(hit, dict) and hit.get("size") == st.st_size and hit.get("mtime_ns") == st.st_mtime_ns:
            return hit["sha256"]
        if log:
            log(f"hashing {path} ({st.st_size} bytes)")
        digest = sha256_file(path)
        self.hashed_now.append(str(path))
        self.cache[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
        self.save()
        return digest

    def cached(self, path: Path) -> str | None:
        try:
            st = Path(path).stat()
        except OSError:
            return None
        hit = self.cache.get(os.path.normcase(str(Path(path).resolve())))
        if isinstance(hit, dict) and hit.get("size") == st.st_size and hit.get("mtime_ns") == st.st_mtime_ns:
            return hit["sha256"]
        return None

    def save(self) -> None:
        try:
            write_json_atomic(self.cache_path, self.cache)
            write_json_atomic(self.refs_path, self.refs)
            write_json_atomic(self.tofu_path, self.tofu)
        except OSError:
            pass  # a read-only models dir still verifies; it only re-hashes next time


def resolve_in(models_dir: Path, rel: str) -> Path:
    root = Path(models_dir).resolve()
    lexical = root.joinpath(*PurePosixPath(rel).parts)
    if lexical.is_symlink():
        raise PermissionError(f"{rel}: symlink inside the models directory is not accepted")
    if root not in lexical.parents:
        raise PermissionError(f"{rel}: escapes the models directory")
    return lexical


def locate_present(models_dir: Path, spec: dict, state: State) -> Path | None:
    """The file on disk for this entry: exact name, a recorded reference, or a unique glob match."""
    if spec.get("name"):
        rel = _file_rel(spec)
        path = resolve_in(models_dir, rel)
        if path.is_file():
            return path
        ref = state.refs.get(rel)
        if ref and Path(ref).is_file():
            return Path(ref)
        return None
    glob = spec.get("name_glob")
    folder = resolve_in(models_dir, spec["dest_dir"])
    if glob and folder.is_dir():
        hits = sorted(p for p in folder.iterdir() if p.is_file() and fnmatch.fnmatchcase(p.name, glob))
        if len(hits) == 1:
            return hits[0]
    return None


def find_reuse_candidate(reuse_dirs: list[Path], spec: dict) -> Path | None:
    """Same relative path first, then the same file name / glob anywhere under the reuse dir."""
    for base in reuse_dirs:
        base = Path(base)
        if not base.is_dir():
            continue
        if spec.get("name"):
            direct = base.joinpath(*PurePosixPath(_file_rel(spec)).parts)
            if direct.is_file():
                return direct
            flat = base / spec["name"]
            if flat.is_file():
                return flat
        pattern = spec.get("name") or spec.get("name_glob")
        hits = sorted(p for p in base.rglob("*") if p.is_file() and fnmatch.fnmatchcase(p.name, pattern)
                      and not p.name.endswith(".part") and STATE_DIR not in p.parts)
        if len(hits) == 1:
            return hits[0]
    return None


def free_bytes(path: Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


# ----------------------------------------------------------------------------- download

class Cancelled(Exception):
    pass


class DownloadError(Exception):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


def file_url(prof: dict, spec: dict, revision: str | None = None) -> str | None:
    if spec.get("url"):
        return spec["url"]
    src = prof.get("source") or {}
    repo = src.get("hf_repo")
    rev = revision or src.get("revision")
    name = spec.get("hf_path") or spec.get("name")
    if not (repo and rev and name):
        return None
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    return f"{endpoint}/{repo}/resolve/{urllib.parse.quote(rev, safe='')}/{urllib.parse.quote(name)}"


def stream_to_part(url: str, part: Path, *, expected_size: int | None, stop, chunk: int = DEFAULT_CHUNK,
                   timeout: int = 60, log=None) -> dict:
    """Download url into part, resuming with HTTP Range when part already has bytes.

    Returns {"resumed": bool, "range_supported": bool|None, "bytes": total}.  A server that answers a
    Range request with 200 is treated as not supporting resume: the part restarts from zero.
    Cancelled (STOP file / Ctrl+C) leaves the part in place for the next run."""
    if not url.lower().startswith(("https://", "http://")):
        raise DownloadError(FAILED_NETWORK, f"unsupported url scheme: {url}")
    start = part.stat().st_size if part.is_file() else 0
    if expected_size is not None and start > expected_size:
        part.unlink()
        start = 0
    if expected_size is not None and start == expected_size:
        return {"resumed": True, "range_supported": None, "bytes": start}
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise DownloadError(FAILED_NETWORK, f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise DownloadError(FAILED_NETWORK, f"{type(exc).__name__}: {exc}") from exc
    resumed, range_supported, mode = False, None, "wb"
    with response:
        code = response.status
        if start:
            if code == 206:
                m = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
                if not m or int(m.group(1)) != start:
                    raise DownloadError(FAILED_NETWORK, f"bad Content-Range {response.headers.get('Content-Range')!r}")
                if expected_size is not None and m.group(3) != "*" and int(m.group(3)) != expected_size:
                    raise DownloadError(FAILED_SIZE, f"server size {m.group(3)} != manifest {expected_size}")
                resumed, range_supported, mode = True, True, "ab"
            elif code == 200:
                range_supported, start = False, 0
                if log:
                    log(f"  server ignored Range: restarting {part.name} from zero")
            else:
                raise DownloadError(FAILED_NETWORK, f"unexpected HTTP {code} for a Range request")
        elif code != 200:
            raise DownloadError(FAILED_NETWORK, f"unexpected HTTP {code}")
        length = response.headers.get("Content-Length")
        if expected_size is not None and length is not None and length.isdigit() and start + int(length) != expected_size:
            raise DownloadError(FAILED_SIZE, f"server announces {start + int(length)} bytes, manifest {expected_size}")
        total = start
        with part.open(mode) as fh:
            try:
                while True:
                    block = response.read(chunk)
                    if not block:
                        break
                    fh.write(block)
                    total += len(block)
                    if expected_size is not None and total > expected_size:
                        raise DownloadError(FAILED_SIZE, f"received more than manifest {expected_size} bytes")
                    if stop():  # checked after each written chunk: what arrived is kept for the resume
                        fh.flush()
                        raise Cancelled(f"stopped at {total} bytes")
            except KeyboardInterrupt as exc:
                raise Cancelled(f"Ctrl+C at {total} bytes") from exc
            except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
                raise DownloadError(FAILED_NETWORK, f"transfer interrupted at {total} bytes: {exc}") from exc
    announced = start + int(length) if length is not None and length.isdigit() else None
    short_of = expected_size if expected_size is not None else announced
    if short_of is not None and total < short_of:
        # the server closed early: keep the part, the next run resumes it
        raise DownloadError(FAILED_NETWORK, f"connection closed at {total} of {short_of} bytes (part kept)")
    return {"resumed": resumed, "range_supported": range_supported, "bytes": total}


# ----------------------------------------------------------------------------- core

class Runner:
    def __init__(self, manifest: dict, models_dir: Path, *, reuse_dirs=(), allow_unpinned=False,
                 margin=DEFAULT_MARGIN, stop_file: Path | None = None, chunk=DEFAULT_CHUNK, log=_log,
                 hash_in_plan=False):
        self.manifest = manifest
        self.models_dir = Path(models_dir)
        self.reuse_dirs = [Path(d) for d in reuse_dirs]
        self.allow_unpinned = allow_unpinned
        self.margin = margin
        self.stop_file = Path(stop_file) if stop_file else self.models_dir / STOP_NAME
        self.chunk = chunk
        self.log = log
        self.hash_in_plan = hash_in_plan
        self.state = State(self.models_dir)
        self.cancelled = False

    def stop_requested(self) -> bool:
        return self.cancelled or self.stop_file.exists()

    # ---- plan (no network)
    def plan_file(self, prof: dict, spec: dict) -> dict:
        pinned = spec["status"] == "PINNED"
        row = {"file_id": spec["id"], "name": spec.get("name") or spec.get("name_glob"), "dest_dir": spec["dest_dir"],
               "pinned": pinned, "size_bytes": spec.get("size_bytes"), "state": None, "bytes_to_download": 0,
               "reuse_from": None}
        present = locate_present(self.models_dir, spec, self.state)
        if present is not None:
            row["path"] = str(present)
            size = present.stat().st_size
            row["bytes_present"] = size
            if not pinned:
                row["state"] = "PRESENT_UNPINNED"
                return row
            if size != spec["size_bytes"]:
                row["state"] = "PRESENT_SIZE_MISMATCH"
            else:
                digest = self.state.sha256(present, self.log) if self.hash_in_plan else self.state.cached(present)
                if digest is None:
                    row["state"] = "PRESENT_SIZE_OK_NOT_HASHED"
                    return row
                row["state"] = "PRESENT_VALID" if digest == spec["sha256"] else "PRESENT_HASH_MISMATCH"
                if row["state"] == "PRESENT_VALID":
                    return row
        candidate = find_reuse_candidate(self.reuse_dirs, spec)
        if candidate is not None and (not pinned or candidate.stat().st_size == spec["size_bytes"]):
            cached = self.state.cached(candidate)
            if not pinned or cached in (None, spec["sha256"]):
                row["reuse_from"] = str(candidate)
                row["state"] = row["state"] or "REUSABLE"
                return row
        if not pinned:
            row["state"] = row["state"] or "UNPINNED_MISSING"
            return row
        part = self._part_path(spec)
        part_bytes = part.stat().st_size if part.is_file() else 0
        row["part_bytes"] = part_bytes
        row["bytes_to_download"] = max(spec["size_bytes"] - part_bytes, 0)
        row["state"] = row["state"] or ("PARTIAL" if part_bytes else "MISSING")
        return row

    def _part_path(self, spec: dict) -> Path:
        dest = resolve_in(self.models_dir, _file_rel(spec))
        return dest.with_name(dest.name + ".part")

    def plan(self, profiles: list[dict]) -> dict:
        out, total, unknown = [], 0, []
        for prof in profiles:
            rows = [self.plan_file(prof, spec) for spec in prof["files"]]
            need = sum(r["bytes_to_download"] for r in rows)
            unknown += [r["file_id"] for r in rows if r["state"] == "UNPINNED_MISSING"]
            total += need
            out.append({"id": prof["id"], "optional": prof["optional"], "status": prof["status"],
                        "reuse_only": bool(prof.get("reuse_only")), "bytes_to_download": need, "files": rows})
        free = free_bytes(self.models_dir)
        required = self.required_free(out)
        return {"action": "plan", "models_dir": str(self.models_dir), "profiles": out, "bytes_to_download": total,
                "free_bytes": free, "margin_bytes": self.margin, "required_free_bytes": required,
                "fits": free >= required, "unknown_size_files": unknown,
                "fits_note": ("counts pinned sizes only; UNPINNED files have no known size" if unknown else None),
                "downloads_performed": 0}

    def required_free(self, plan_profiles: list[dict]) -> int:
        need = sum(p["bytes_to_download"] for p in plan_profiles)
        if need == 0:
            return 0
        by_id = {p["id"]: p for p in self.manifest["profiles"]}
        floors = [by_id[p["id"]]["min_free_disk"]["bytes"] or 0 for p in plan_profiles if p["bytes_to_download"]]
        return max([need + self.margin] + floors)

    # ---- fetch
    def fetch(self, profiles: list[dict]) -> dict:
        if self.stop_file.exists():
            return {"action": "fetch", "verdict": "REFUSED", "exit_code": EXIT_REFUSED,
                    "error": f"STOP file present: {self.stop_file} — remove it to start", "profiles": []}
        plan = self.plan(profiles)
        free = plan["free_bytes"]
        required = [p for p in plan["profiles"] if not p["optional"]]
        optional = [p for p in plan["profiles"] if p["optional"]]
        need_required = self.required_free(required)
        if free < need_required:
            return {"action": "fetch", "verdict": "REFUSED", "exit_code": EXIT_REFUSED, "error": DISK_INSUFFICIENT,
                    "free_bytes": free, "required_free_bytes": need_required, "downloads_performed": 0,
                    "profiles": plan["profiles"]}
        accepted, skipped_disk = list(required), set()
        for p in optional:
            if free >= self.required_free(accepted + [p]):
                accepted.append(p)
            else:
                skipped_disk.add(p["id"])
        results = []
        for prof in profiles:
            if prof["id"] in skipped_disk:
                files = [{"file_id": s["id"], "state": SKIPPED_OPTIONAL_FAILURE, "cause": DISK_INSUFFICIENT}
                         for s in prof["files"]]
                results.append(self._profile_result(prof, files))
                continue
            files = []
            for spec in prof["files"]:
                if self.stop_requested():
                    self.cancelled = True
                    files.append({"file_id": spec["id"], "state": NOT_STARTED})
                    continue
                files.append(self.fetch_file(prof, spec))
            results.append(self._profile_result(prof, files))
        self.state.save()
        return self._summary("fetch", results, extra={"free_bytes_before": free})

    def fetch_file(self, prof: dict, spec: dict) -> dict:
        pinned = spec["status"] == "PINNED"
        row = {"file_id": spec["id"], "name": spec.get("name") or spec.get("name_glob")}
        present = locate_present(self.models_dir, spec, self.state)
        if present is not None:
            checked = self._check_present(spec, present)
            if checked["state"] in (VERIFIED, UNVERIFIED_TOFU):
                checked["state"] = REUSED if checked["state"] == VERIFIED else UNVERIFIED_TOFU
                checked["mode"] = "present"
                return {**row, **checked}
            if checked["state"] == FAILED_HASH and not pinned:
                return {**row, **checked}  # a contradicting observed prefix/TOFU hash is evidence of damage
            row["replaces_bad_file"] = str(present)
        candidate = find_reuse_candidate(self.reuse_dirs, spec)
        if candidate is not None:
            checked = self._check_present(spec, candidate)
            if checked["state"] in (VERIFIED, UNVERIFIED_TOFU) and spec.get("name"):
                mode = self._adopt(spec, candidate)
                return {**row, **checked, "state": REUSED if checked["state"] == VERIFIED else UNVERIFIED_TOFU,
                        "mode": mode, "reuse_from": str(candidate)}
            if checked["state"] in (VERIFIED, UNVERIFIED_TOFU):
                return {**row, **checked, "state": UNVERIFIED_TOFU, "mode": "reference-glob",
                        "reuse_from": str(candidate)}
            row["reuse_rejected"] = {"path": str(candidate), "state": checked["state"]}
        if not pinned:
            if not self.allow_unpinned:
                return {**row, "state": UNPINNED_REFUSED,
                        "detail": spec.get("unpinned_reason") or "no sha256 in the manifest"}
            url = file_url(prof, spec, revision=(prof.get("source") or {}).get("revision") or "main")
            if not url or not spec.get("name"):
                return {**row, "state": UNPINNED_REFUSED,
                        "detail": "exact file name/url unknown — run `pin` first"}
            return {**row, **self._download(spec, url, verify=False)}
        url = file_url(prof, spec)
        if not url:
            return {**row, "state": FAILED_NETWORK, "detail": "no url and no pinned HF revision"}
        return {**row, **self._download(spec, url, verify=True)}

    def _adopt(self, spec: dict, candidate: Path) -> str:
        dest = resolve_in(self.models_dir, _file_rel(spec))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        try:
            os.link(candidate, dest)
            return "hardlink"
        except OSError:
            self.state.refs[_file_rel(spec)] = str(Path(candidate).resolve())
            self.state.save()
            return "reference"

    def _download(self, spec: dict, url: str, *, verify: bool) -> dict:
        dest = resolve_in(self.models_dir, _file_rel(spec))
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        meta_path = dest.with_name(dest.name + ".part.json")
        meta = {"url_file": spec.get("hf_path") or spec.get("name"), "sha256": spec.get("sha256"),
                "size_bytes": spec.get("size_bytes")}
        if part.exists() and _read_json(meta_path, None) != meta:
            part.unlink()  # a part of another file version is never resumed
        write_json_atomic(meta_path, meta)
        if self.log:
            self.log(f"downloading {spec['id']} -> {_file_rel(spec)}")
        try:
            info = stream_to_part(url, part, expected_size=spec.get("size_bytes") if verify else None,
                                  stop=self.stop_requested, chunk=self.chunk, log=self.log)
        except Cancelled as exc:
            self.cancelled = True
            return {"state": CANCELLED, "detail": str(exc), "part": str(part),
                    "part_bytes": part.stat().st_size if part.exists() else 0}
        except DownloadError as exc:
            if exc.state == FAILED_SIZE:
                part.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
            return {"state": exc.state, "detail": str(exc)[:300], "part": str(part) if part.exists() else None}
        size = part.stat().st_size
        observed = sha256_file(part)
        if verify:
            if size != spec["size_bytes"]:
                part.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                return {"state": FAILED_SIZE, "detail": f"size {size} != manifest {spec['size_bytes']}"}
            if observed != spec["sha256"]:
                part.unlink(missing_ok=True)  # never promoted, never resumed from
                meta_path.unlink(missing_ok=True)
                return {"state": FAILED_HASH, "sha256_observed": observed, "sha256_expected": spec["sha256"]}
        elif spec.get("sha256_prefix") and not observed.startswith(spec["sha256_prefix"]):
            part.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return {"state": FAILED_HASH, "sha256_observed": observed, "detail": "contradicts sha256_prefix"}
        os.replace(part, dest)
        meta_path.unlink(missing_ok=True)
        st = dest.stat()
        self.state.cache[os.path.normcase(str(dest.resolve()))] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                                                   "sha256": observed}
        result = {"path": str(dest), "bytes": size, "sha256_observed": observed,
                  "range_supported": info["range_supported"]}
        if not verify:
            self.state.tofu[_file_rel(spec)] = {"sha256": observed, "size": size, "recorded_at": _now(), "via": "download"}
            return {**result, "state": UNVERIFIED_TOFU, "detail": "downloaded with --allow-unpinned: not verified"}
        return {**result, "state": RESUMED if info["resumed"] else DOWNLOADED}

    # ---- verify
    def _check_present(self, spec: dict, path: Path) -> dict:
        size = path.stat().st_size
        out = {"path": str(path), "bytes": size}
        if spec["status"] == "PINNED":
            if size != spec["size_bytes"]:
                return {**out, "state": FAILED_SIZE, "detail": f"size {size} != manifest {spec['size_bytes']}"}
            observed = self.state.sha256(path, self.log)
            out["sha256_observed"] = observed
            return {**out, "state": VERIFIED if observed == spec["sha256"] else FAILED_HASH}
        observed = self.state.sha256(path, self.log)
        out["sha256_observed"] = observed
        prefix = spec.get("sha256_prefix")
        if prefix:
            out["sha256_prefix_match"] = observed.startswith(prefix)
            if not out["sha256_prefix_match"]:
                return {**out, "state": FAILED_HASH, "detail": f"observed sha256 contradicts recorded prefix {prefix}"}
        key = _file_rel(spec) or f"{spec['dest_dir']}/{path.name}"
        lock = self.state.tofu.get(key)
        if lock and (lock.get("sha256") != observed or lock.get("size") != size):
            return {**out, "state": FAILED_HASH, "detail": f"changed since TOFU record {lock.get('recorded_at')}"}
        if not lock:
            self.state.tofu[key] = {"sha256": observed, "size": size, "recorded_at": _now(), "via": "verify"}
        return {**out, "state": UNVERIFIED_TOFU,
                "detail": "UNPINNED: hash recorded locally (trust on first use), not checked against a pinned source"}

    def verify(self, profiles: list[dict]) -> dict:
        results = []
        for prof in profiles:
            files = []
            for spec in prof["files"]:
                row = {"file_id": spec["id"], "name": spec.get("name") or spec.get("name_glob")}
                present = locate_present(self.models_dir, spec, self.state)
                if present is None:
                    files.append({**row, "state": MISSING})
                else:
                    files.append({**row, **self._check_present(spec, present)})
            results.append(self._profile_result(prof, files))
        self.state.save()
        return self._summary("verify", results)

    # ---- aggregation
    def _profile_result(self, prof: dict, files: list[dict]) -> dict:
        if prof["optional"]:
            for f in files:
                if f["state"] in FAILURE_STATES:
                    f["cause"], f["state"] = f["state"], SKIPPED_OPTIONAL_FAILURE
        states = {f["state"] for f in files}
        if CANCELLED in states or NOT_STARTED in states:
            verdict = CANCELLED
        elif states & FAILURE_STATES:
            verdict = "FAILED"
        elif SKIPPED_OPTIONAL_FAILURE in states:
            verdict = SKIPPED_OPTIONAL_FAILURE
        elif UNVERIFIED_TOFU in states:
            verdict = "UNVERIFIED"
        elif prof["status"] == "REFERENCE" or not files:
            verdict = "REFERENCE" if prof["status"] == "REFERENCE" else "NO_FILES"
        else:
            verdict = "OK"
        return {"id": prof["id"], "optional": prof["optional"], "status": prof["status"], "verdict": verdict,
                "files": files}

    def _summary(self, action: str, results: list[dict], extra: dict | None = None) -> dict:
        required = [r for r in results if not r["optional"]]
        if self.cancelled or any(r["verdict"] == CANCELLED for r in results):
            verdict, code = CANCELLED, EXIT_CANCELLED
        elif any(r["verdict"] == "FAILED" for r in required):
            bad = {f["state"] for r in required for f in r["files"] if f["state"] in FAILURE_STATES}
            # nothing broke, the tool only declined to install unverifiable files
            verdict, code = ("REFUSED", EXIT_REFUSED) if bad == {UNPINNED_REFUSED} else ("FAILED", EXIT_FAILED)
        elif any(r["verdict"] == "UNVERIFIED" for r in required):
            verdict, code = "UNVERIFIED", EXIT_UNVERIFIED
        else:
            verdict, code = "OK", EXIT_OK
        return {"action": action, "verdict": verdict, "exit_code": code, "models_dir": str(self.models_dir),
                "finished_at": _now(), "profiles": results, "hashed_now": self.state.hashed_now, **(extra or {})}


# ----------------------------------------------------------------------------- pin (HF API)

def _get_json(url: str, timeout: int = 60, *, pages: bool = False):
    """GET JSON; with pages=True follow `Link: <...>; rel="next"` (HF tree API pagination)."""
    items, seen = [], 0
    while url:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            link = response.headers.get("Link") or ""
        if not pages:
            return body
        if not isinstance(body, list):
            raise ValueError("tree answer is not a list")
        items.extend(body)
        m = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
        url = urllib.parse.urljoin(url, m.group(1)) if m else None
        seen += 1
        if seen > 1000:
            raise ValueError("tree pagination does not end")
    return items


def pin_manifest(manifest: dict, profile_ids: list[str], endpoint: str, log=_log) -> tuple[dict, list[dict]]:
    """Return (new manifest, report).  Only UNPINNED files of HF-sourced profiles are touched; a hash
    that contradicts an observed sha256_prefix is refused (PIN_CONFLICT), never written."""
    data = json.loads(json.dumps(manifest))
    endpoint = endpoint.rstrip("/")
    report = []
    for prof in select_profiles(data, profile_ids):
        repo = (prof.get("source") or {}).get("hf_repo")
        entry = {"id": prof["id"], "repo": repo, "state": None, "files": []}
        report.append(entry)
        if not repo or not any(f["status"] == "UNPINNED" for f in prof["files"]):
            entry["state"] = "NOTHING_TO_PIN"
            continue
        rev_wanted = prof["source"].get("revision") or "main"
        try:
            info = _get_json(f"{endpoint}/api/models/{repo}/revision/{urllib.parse.quote(rev_wanted, safe='')}")
            commit = info.get("sha")
            if not isinstance(commit, str) or not COMMIT.match(commit):
                raise ValueError(f"no commit sha in revision answer: {commit!r}")
            tree = _get_json(f"{endpoint}/api/models/{repo}/tree/{commit}?recursive=1", pages=True)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            entry["state"] = "PIN_FAILED"
            entry["detail"] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        files_in_repo = [t for t in tree if isinstance(t, dict) and t.get("type") == "file"]
        new_files, conflict = [], False
        for spec in prof["files"]:
            if spec["status"] == "PINNED":
                new_files.append(spec)
                continue
            pattern = spec.get("hf_path") or spec.get("name")
            if pattern:
                hits = [t for t in files_in_repo if t["path"] == pattern or t["path"].rsplit("/", 1)[-1] == pattern]
            else:
                hits = [t for t in files_in_repo if fnmatch.fnmatchcase(t["path"].rsplit("/", 1)[-1], spec["name_glob"])]
            hits.sort(key=lambda t: t["path"])
            if not hits:
                entry["files"].append({"file_id": spec["id"], "state": "NOT_IN_REPO"})
                new_files.append(spec)
                continue
            for i, hit in enumerate(hits):
                lfs = hit.get("lfs") or {}
                digest = lfs.get("oid") or lfs.get("sha256")
                pinned = dict(spec)
                if len(hits) > 1:
                    pinned["id"] = f"{spec['id']}-{i + 1:02d}"
                pinned["name"] = hit["path"].rsplit("/", 1)[-1]
                pinned["hf_path"] = hit["path"]
                pinned.pop("name_glob", None)
                if not (isinstance(digest, str) and HEX64.match(digest)):
                    pinned["unpinned_reason"] = "file is not stored in LFS: the HF API gives no sha256"
                    entry["files"].append({"file_id": pinned["id"], "state": "NO_LFS_SHA256"})
                    new_files.append(pinned)
                    continue
                if spec.get("sha256_prefix") and not digest.startswith(spec["sha256_prefix"]):
                    conflict = True
                    entry["files"].append({"file_id": pinned["id"], "state": "PIN_CONFLICT",
                                           "detail": f"HF sha256 {digest[:12]}… contradicts observed prefix {spec['sha256_prefix']}"})
                    new_files.append(spec)
                    break
                pinned.update({"status": "PINNED", "sha256": digest, "size_bytes": int(lfs.get("size", hit.get("size"))),
                               "pinned_via": "hf-api", "pinned_at": _now()})
                pinned.pop("unpinned_reason", None)
                entry["files"].append({"file_id": pinned["id"], "state": "PINNED", "sha256": digest})
                new_files.append(pinned)
        if conflict:
            entry["state"] = "PIN_CONFLICT"
            continue
        prof["files"] = new_files
        prof["source"]["revision"] = commit
        statuses = {f["status"] for f in new_files}
        prof["status"] = "PINNED" if statuses == {"PINNED"} else ("PARTIALLY_PINNED" if "PINNED" in statuses else "UNPINNED")
        if prof["status"] == "PINNED":
            prof.pop("status_reason", None)
            total = sum(f["size_bytes"] for f in new_files)
            prof["min_free_disk"] = {"bytes": total + DEFAULT_MARGIN,
                                     "basis": f"sum of pinned sizes + {DEFAULT_MARGIN // 1024 ** 3} GiB margin"}
        else:
            prof["status_reason"] = "pin: some files could not be pinned from the HF API"
        lic = (info.get("cardData") or {}).get("license")
        if lic:
            prof["license"]["id_from_hf_card"] = lic
        entry["state"] = prof["status"]
        entry["revision"] = commit
        if log:
            log(f"pinned {prof['id']} @ {commit[:12]}: {prof['status']}")
    data["pinned_at"] = _now()
    return validate_manifest(data), report


# ----------------------------------------------------------------------------- runtime-check

def _expand(template: str) -> str | None:
    missing = []

    def sub(m):
        value = os.environ.get(m.group(1) or m.group(2))
        if value is None:
            missing.append(m.group(0))
            return ""
        return value
    out = re.sub(r"\$\{(\w+)\}|%(\w+)%", sub, template)
    return None if missing else out


def runtime_check(manifest: dict, profiles: list[dict], runtime_bin: str | None, timeout: int = 30) -> dict:
    rows = []
    for prof in profiles:
        rid = prof["runtime"]["ref"]
        rt = manifest["runtimes"][rid]
        row = {"profile": prof["id"], "runtime": rid, "engine": rt["engine"], "expected_version": rt["version"]}
        if not rt.get("version_regex"):
            rows.append({**row, "state": "RUNTIME_UNPINNED",
                         "detail": rt.get("status_reason") or "no version probe defined; nothing to check"})
            continue
        candidates = [runtime_bin] if runtime_bin else []
        env_name = rt.get("binary_env")
        if not runtime_bin and env_name and os.environ.get(env_name):
            candidates.append(os.environ[env_name])
        if not runtime_bin:
            candidates += [c for c in (_expand(t) for t in rt.get("binary_candidates") or []) if c]
        binary = next((c for c in candidates if Path(c).is_file()), None)
        row["searched"] = candidates
        if binary is None:
            rows.append({**row, "state": "RUNTIME_MISSING"})
            continue
        row["binary"] = binary
        try:
            proc = subprocess.run([binary, *rt.get("version_args", ["--version"])], capture_output=True, text=True,
                                  timeout=timeout, errors="replace")
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            rows.append({**row, "state": "RUNTIME_VERSION_UNKNOWN", "detail": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        m = re.search(rt["version_regex"], text)
        if not m:
            rows.append({**row, "state": "RUNTIME_VERSION_UNKNOWN", "output_tail": text.strip()[-300:]})
            continue
        observed = m.group(1)
        row["observed_version"] = observed
        state = "RUNTIME_OK" if observed == str(rt.get("version_match", rt["version"])) else "RUNTIME_VERSION_MISMATCH"
        if state == "RUNTIME_OK" and rt.get("binary_sha256"):
            if sha256_file(Path(binary)) != rt["binary_sha256"]:
                state = "RUNTIME_BAD_HASH"
        rows.append({**row, "state": state})
    required_bad = [r for r, p in zip(rows, profiles) if not p["optional"]
                    and r["state"] not in ("RUNTIME_OK", "RUNTIME_UNPINNED")]
    return {"action": "runtime-check", "verdict": "FAILED" if required_bad else "OK",
            "exit_code": EXIT_FAILED if required_bad else EXIT_OK, "runtimes": rows,
            "note": "read-only: nothing is installed, drivers/BIOS/system Python are never touched"}


# ----------------------------------------------------------------------------- CLI

def _default_manifest() -> Path:
    return Path(__file__).resolve().with_name("model_profiles.json")


def _print(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    for prof in data.get("profiles") or []:
        print(f"[{prof.get('verdict', prof.get('status'))}] {prof['id']}{' (optional)' if prof.get('optional') else ''}")
        for f in prof.get("files") or []:
            cause = f" <- {f['cause']}" if f.get("cause") else ""
            print(f"    {f.get('state'):<28}{cause} {f.get('file_id')}")
    for r in data.get("runtimes") or []:
        print(f"{r['state']:<26} {r['profile']} -> {r['runtime']}")
    for key in ("bytes_to_download", "free_bytes", "required_free_bytes", "fits", "error"):
        if key in data:
            print(f"{key}: {data[key]}")
    print(f"verdict: {data.get('verdict', 'PLAN')}")


def main(argv=None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("validate", "plan", "fetch", "verify", "pin", "runtime-check"))
    parser.add_argument("--manifest", default=None, help="model_profiles.json (default: next to this script)")
    parser.add_argument("--models-dir", default=None,
                        help="default: BOSSMAN_MODELS_DIR, else %%LOCALAPPDATA%%\\Bossman\\models")
    parser.add_argument("--profile", action="append", default=[], help="profile id (repeatable); default: all required")
    parser.add_argument("--reuse-dir", action="append", default=[], help="existing model folder to reuse from (repeatable)")
    parser.add_argument("--allow-unpinned", action="store_true",
                        help="fetch UNPINNED files anyway; the result is UNVERIFIED_TOFU, never verified")
    parser.add_argument("--margin-bytes", type=int, default=DEFAULT_MARGIN, help="free-disk margin on top of the download")
    parser.add_argument("--stop-file", default=None, help=f"cancel when this file appears (default <models>/{STOP_NAME})")
    parser.add_argument("--hash", action="store_true", help="plan: hash present files not in the cache")
    parser.add_argument("--studio-models", default=None, help="studio_models.json for the photo/video reference contract")
    parser.add_argument("--hf-endpoint", default=None, help="pin: HF API base (default HF_ENDPOINT or https://huggingface.co)")
    parser.add_argument("--out", default=None, help="pin: output manifest path (required)")
    parser.add_argument("--report", default=None, help="also write the JSON report to this file")
    parser.add_argument("--runtime-bin", default=None, help="runtime-check: binary to probe")
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    def finish(report: dict, code: int) -> int:
        report.setdefault("exit_code", code)
        if args.report:
            write_json_atomic(Path(args.report), report)
        _print(report, args.json)
        return code

    manifest_path = Path(args.manifest) if args.manifest else _default_manifest()
    try:
        catalog = find_studio_catalog(args.studio_models)
        manifest = load_manifest(manifest_path, catalog)
        profiles = select_profiles(manifest, args.profile)
    except ValueError as exc:
        return finish({"action": args.action, "verdict": "INVALID", "error": str(exc)[:4000]}, EXIT_INVALID)
    if args.action == "validate":
        return finish({"action": "validate", "verdict": "OK", "manifest": str(manifest_path),
                       "profiles": [{"id": p["id"], "status": p["status"], "optional": p["optional"]}
                                    for p in manifest["profiles"]],
                       "studio_catalog_checked": catalog is not None}, EXIT_OK)
    if args.action == "runtime-check":
        report = runtime_check(manifest, profiles, args.runtime_bin)
        return finish(report, report["exit_code"])
    if args.action == "pin":
        if not args.out:
            return finish({"action": "pin", "verdict": "INVALID", "error": "--out is required"}, EXIT_INVALID)
        endpoint = args.hf_endpoint or os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
        try:
            pinned, report = pin_manifest(manifest, args.profile, endpoint)
        except ValueError as exc:
            return finish({"action": "pin", "verdict": "INVALID", "error": str(exc)[:2000]}, EXIT_INVALID)
        bad = [r for r in report if r["state"] in ("PIN_FAILED", "PIN_CONFLICT")]
        write_json_atomic(Path(args.out), pinned)
        return finish({"action": "pin", "verdict": "FAILED" if bad else "OK", "out": args.out, "report": report},
                      EXIT_FAILED if bad else EXIT_OK)
    models_dir = Path(args.models_dir).expanduser() if args.models_dir else default_models_dir()
    runner = Runner(manifest, models_dir, reuse_dirs=args.reuse_dir, allow_unpinned=args.allow_unpinned,
                    margin=args.margin_bytes, stop_file=Path(args.stop_file) if args.stop_file else None,
                    chunk=args.chunk_bytes, hash_in_plan=args.hash)
    if args.action == "plan":
        report = runner.plan(profiles)
        return finish(report, EXIT_OK)
    if args.action == "verify":
        report = runner.verify(profiles)
        return finish(report, report["exit_code"])
    models_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = runner.fetch(profiles)
    except KeyboardInterrupt:
        runner.state.save()
        report = {"action": "fetch", "verdict": CANCELLED, "exit_code": EXIT_CANCELLED, "detail": "Ctrl+C"}
    return finish(report, report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
