"""Add-on installer for the calls dependencies on the Windows embedded runtime (which ships without pip).

The Windows bundle is hash-locked and must not be edited by hand, so Telegram calls are an OPTIONAL add-on: this module
downloads exactly the files in ``addon_lock.json`` (each pinned by sha256), verifies them, and unpacks them into
``<data_dir>/addons/telegram-calls/<lock hash>/site-packages``. ``activate()`` puts that directory on ``sys.path`` for the
worker (and for ``deps.probe``), so nothing is installed into the bundle itself and removing the add-on is deleting a folder.

Safety: HTTPS URLs from the committed lock only, sha256 verified BEFORE anything is written, archives are unpacked
into a temporary directory with path-traversal / symlink / size / count limits and then moved into place atomically.
Packages the bundle already provides at the pinned version are skipped. On other platforms use
``pip install "bossman-command-center[calls]"`` (pinned extra); ``status()`` says which case applies.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from .types import CallError

LOCK_PATH = Path(__file__).with_name("addon_lock.json")
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
MAX_MEMBERS = 8000
MANIFEST = "manifest.json"

Fetch = Callable[[str, int], bytes]


def load_lock(path: Path | None = None) -> dict:
    lock = json.loads((path or LOCK_PATH).read_text(encoding="utf-8"))
    if lock.get("schema") != 1 or not isinstance(lock.get("packages"), list):
        raise CallError("DEPENDENCIES_MISSING", detail="addon_lock_invalid")
    for p in lock["packages"]:
        if not str(p.get("url", "")).startswith("https://") or len(str(p.get("sha256", ""))) != 64:
            raise CallError("DEPENDENCIES_MISSING", detail="addon_lock_invalid")
    return lock


def lock_hash(lock: dict) -> str:
    canonical = json.dumps(lock["packages"], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:12]


def current_target() -> str:
    tag = "win_amd64" if sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64") else f"{sys.platform}-{platform.machine().lower()}"
    return f"{tag}-cp{sys.version_info.major}{sys.version_info.minor}"


def addon_root(data_dir: Path | None = None) -> Path:
    from .settings import data_dir as _data_dir
    return (data_dir or _data_dir()) / "addons" / "telegram-calls"


def site_dir(lock: dict, data_dir: Path | None = None) -> Path:
    return addon_root(data_dir) / lock_hash(lock) / "site-packages"


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def missing(lock: dict) -> list[dict]:
    """Lock entries not already provided (at the pinned version) by the running interpreter."""
    return [p for p in lock["packages"] if _installed_version(p["name"]) != p["version"]]


def activate(data_dir: Path | None = None, lock: dict | None = None) -> Path | None:
    """Put an installed add-on on ``sys.path`` (idempotent). Returns the directory or None."""
    try:
        lock = lock or load_lock()
        target = site_dir(lock, data_dir)
    except Exception:  # noqa: BLE001 - no add-on is a valid state
        return None
    if not (target.parent / MANIFEST).is_file():
        return None
    text = str(target)
    if text not in sys.path:
        sys.path.insert(0, text)
        importlib.invalidate_caches()
    return target


def status(data_dir: Path | None = None) -> dict:
    lock = load_lock()
    activate(data_dir, lock)
    todo = missing(lock)
    supported = lock.get("target") == current_target()
    return {"lock": lock_hash(lock), "target": lock.get("target"), "this_platform": current_target(),
            "installer_supported": supported, "complete": not todo, "missing": [f'{p["name"]}=={p["version"]}' for p in todo],
            "path": str(site_dir(lock, data_dir)),
            "remedy": None if not todo else ("bossman call install" if supported else 'pip install "bossman-command-center[calls]"')}


def _default_fetch(url: str, max_bytes: int) -> bytes:
    if not url.startswith("https://"):
        raise CallError("DEPENDENCIES_MISSING", detail="non_https_url")
    req = urllib.request.Request(url, headers={"User-Agent": "bossman-calls-addon"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - https only, fixed by the committed lock
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise CallError("DEPENDENCIES_MISSING", detail="download_too_large")
    return data


def _safe_relative(name: str) -> PurePosixPath | None:
    n = name.replace("\\", "/")
    if not n or n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        return None
    p = PurePosixPath(n)
    if any(part in ("..", "") for part in p.parts):
        return None
    return p


def _unpack_wheel(data: bytes, dest: Path, budget: list[int]) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise CallError("DEPENDENCIES_MISSING", detail="too_many_members")
        for info in infos:
            if info.is_dir():
                continue
            rel = _safe_relative(info.filename)
            if rel is None or (info.external_attr >> 16) & 0o170000 == 0o120000:      # traversal or symlink
                raise CallError("DEPENDENCIES_MISSING", detail="unsafe_archive_member")
            parts = list(rel.parts)
            if parts[0].endswith(".data"):                 # wheel data scheme: only purelib/platlib belong in site-packages
                if len(parts) > 2 and parts[1] in ("purelib", "platlib"):
                    parts = parts[2:]
                else:
                    continue
            budget[0] += info.file_size
            if budget[0] > MAX_UNPACKED_BYTES:
                raise CallError("DEPENDENCIES_MISSING", detail="unpacked_too_large")
            out = dest.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _unpack_sdist(data: bytes, dest: Path, package_dir: str, budget: list[int]) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = tf.getmembers()
        if len(members) > MAX_MEMBERS:
            raise CallError("DEPENDENCIES_MISSING", detail="too_many_members")
        for m in members:
            if not m.isfile():
                if m.issym() or m.islnk() or m.isdev():
                    raise CallError("DEPENDENCIES_MISSING", detail="unsafe_archive_member")
                continue
            rel = _safe_relative(m.name)
            if rel is None:
                raise CallError("DEPENDENCIES_MISSING", detail="unsafe_archive_member")
            parts = list(rel.parts)
            if len(parts) < 2 or parts[1] != package_dir:  # <top>/<package_dir>/...: only the package itself is taken
                continue
            budget[0] += m.size
            if budget[0] > MAX_UNPACKED_BYTES:
                raise CallError("DEPENDENCIES_MISSING", detail="unpacked_too_large")
            out = dest.joinpath(*parts[1:])
            out.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            assert src is not None
            with src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def install(data_dir: Path | None = None, *, fetch: Fetch | None = None, lock: dict | None = None,
            progress: Callable[[str], None] | None = None, allow_other_platform: bool = False) -> dict:
    """Download, verify (sha256) and unpack the add-on. Nothing is written until every file has been verified."""
    lock = lock or load_lock()
    if lock.get("target") != current_target() and not allow_other_platform:
        raise CallError("DEPENDENCIES_MISSING", detail="addon_target_mismatch")
    fetch = fetch or _default_fetch
    todo = missing(lock)
    final = site_dir(lock, data_dir)
    if not todo:
        return {"status": "already_satisfied", "installed": [], "path": str(final)}
    downloaded: list[tuple[dict, bytes]] = []
    for entry in todo:
        if progress:
            progress(f'download {entry["name"]}=={entry["version"]}')
        data = fetch(entry["url"], MAX_FILE_BYTES)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise CallError("DEPENDENCIES_MISSING", detail=f'sha256_mismatch:{entry["name"]}')       # nothing has been written
        downloaded.append((entry, data))
    parent = final.parent
    parent.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".partial-", dir=parent.parent))
    try:
        site = tmp / "site-packages"
        site.mkdir()
        budget = [0]
        for entry, data in downloaded:
            if progress:
                progress(f'unpack {entry["name"]}')
            if entry["kind"] == "wheel":
                _unpack_wheel(data, site, budget)
            elif entry["kind"] == "sdist_pure":
                _unpack_sdist(data, site, entry["package_dir"], budget)
            else:
                raise CallError("DEPENDENCIES_MISSING", detail="unknown_kind")
        (tmp / MANIFEST).write_text(json.dumps({"lock": lock_hash(lock), "target": lock.get("target"), "installed_at": time.time(),
                                                "packages": {e["name"]: {"version": e["version"], "sha256": e["sha256"]} for e, _ in downloaded}}),
                                    encoding="utf-8")
        if parent.exists():
            shutil.rmtree(parent)
        os.replace(tmp, parent)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    activate(data_dir, lock)
    return {"status": "installed", "installed": [f'{e["name"]}=={e["version"]}' for e, _ in downloaded], "path": str(final)}
