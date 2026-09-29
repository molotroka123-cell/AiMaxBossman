"""Installer of the optional call dependencies for an embedded Python that has no pip.

``bossman call install`` (owner-triggered, never automatic, never on import) downloads PINNED wheels and unpacks
them into ``<data_dir>/addons/telegram-calls/packages``. Rules, each with a test:

* every artifact needs a pinned sha256; an artifact marked ``UNPINNED_NEEDS_HASH`` makes the installer REFUSE the
  whole install BEFORE any download (it never "skips verification");
* downloads only over https from ``pypi.org`` / ``files.pythonhosted.org`` (redirects included);
* the sha256 of the downloaded bytes is compared with OUR pin before anything is unpacked; the hash that PyPI
  reports is never trusted, it is only used to locate the file URL;
* the archive is unpacked path-traversal-safe (no absolute paths, ``..``, drive letters, symlinks, zip bombs);
* idempotent: an already installed pinned artifact is not downloaded again;
* nothing here touches a running call, Telegram, credentials or the network at import time.

The worker runs with ``python -I`` (no PYTHONPATH, no user site), so the add-on directory must be added by the
worker itself: call ``activate(data_dir)`` before importing telethon / pytgcalls (see docs/telegram-calls/CONTINUE.md).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

UNPINNED = "UNPINNED_NEEDS_HASH"
ALLOWED_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org"})
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 600 * 1024 * 1024
MAX_MEMBERS = 20000
STATE_FILE = "installed.json"

Downloader = Callable[[str], bytes]


@dataclass(frozen=True)
class Artifact:
    name: str                      # PyPI project name
    version: str
    sha256: str                    # hex, or UNPINNED
    filename: str = ""             # hint only; the file is located by sha256, never trusted by name
    note: str = ""

    @property
    def pinned(self) -> bool:
        return len(self.sha256) == 64 and all(c in "0123456789abcdef" for c in self.sha256)


#: The pin table. Hashes that could not be verified offline are NOT invented: the installer refuses them.
MANIFEST: tuple[Artifact, ...] = (
    Artifact("ntgcalls", "3.0.0", "545cfe0069911cbc22f98b1ac8ba4be42065903e88467b590037aa5514c9beba",
             "ntgcalls-3.0.0 cp312 win_amd64 wheel"),
    Artifact("py-tgcalls", "3.0.0", "c736066f3f79804f6f128231f4adad228e6aefb3418a473e0a52175c0a8baf49",
             "py_tgcalls-3.0.0 wheel"),
    Artifact("telethon", "1.45.0", UNPINNED, note="hash not verifiable offline"),
    Artifact("pyaes", "1.6.1", UNPINNED, note="telethon dependency; version and hash not verifiable offline"),
    Artifact("rsa", "4.9.1", UNPINNED, note="telethon dependency; version and hash not verifiable offline"),
    Artifact("pyasn1", "0.6.1", UNPINNED, note="rsa dependency; version and hash not verifiable offline"),
)


class AddonError(Exception):
    def __init__(self, code: str, message: str, artifact: str = ""):
        super().__init__(code)
        self.code, self.message, self.artifact = code, message, artifact


# ------------------------------------------------------------------ paths / sys.path
def addon_root(data_dir: Path | str) -> Path:
    return Path(data_dir) / "addons" / "telegram-calls"


def addon_path(data_dir: Path | str) -> Path:
    """Directory to put on ``sys.path`` (the unpacked packages). May not exist before the first install."""
    return addon_root(data_dir) / "packages"


def activate(data_dir: Path | str) -> bool:
    """Idempotently make the add-on importable in THIS process (works under ``python -I``). Returns True if present."""
    p = addon_path(data_dir)
    if not p.is_dir():
        return False
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        for d in {p, *(x.parent for x in p.rglob("*.dll"))}:
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
    return True


# ------------------------------------------------------------------ download
def _check_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in ALLOWED_HOSTS or u.username or u.password:
        raise AddonError("HOST_NOT_ALLOWED", "Загрузка разрешена только по https с pypi.org и files.pythonhosted.org.")


class _StrictRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_downloader(url: str) -> bytes:
    _check_url(url)
    opener = urllib.request.build_opener(_StrictRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "bossman-addon-installer"})
    with opener.open(req, timeout=60) as resp:  # noqa: S310 - host allow-list checked above and on every redirect
        _check_url(resp.geturl())
        out = bytearray()
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                return bytes(out)
            out += chunk
            if len(out) > MAX_DOWNLOAD_BYTES:
                raise AddonError("DOWNLOAD_TOO_LARGE", "Файл слишком большой.")


def _locate(art: Artifact, dl: Downloader) -> str:
    """URL of the file whose sha256 equals OUR pin (PyPI's JSON only tells where to fetch, it is not the trust root)."""
    api = f"https://pypi.org/pypi/{urllib.parse.quote(art.name)}/{urllib.parse.quote(art.version)}/json"
    _check_url(api)
    try:
        doc = json.loads(dl(api).decode("utf-8"))
        files = doc.get("urls") or []
    except (ValueError, AttributeError) as exc:
        raise AddonError("DOWNLOAD_FAILED", "Ответ PyPI не разобран.", art.name) from exc
    for f in files:
        if isinstance(f, dict) and str((f.get("digests") or {}).get("sha256", "")).lower() == art.sha256 and f.get("url"):
            url = str(f["url"])
            _check_url(url)
            return url
    raise AddonError("WHEEL_NOT_FOUND", f"На PyPI нет файла {art.name}=={art.version} с закреплённым sha256.", art.name)


# ------------------------------------------------------------------ safe unpack
def _safe_target(root: Path, member: str) -> Path:
    name = member.replace("\\", "/")
    p = PurePosixPath(name)
    if (not name or name.startswith("/") or p.is_absolute() or ".." in p.parts or ":" in (p.parts[0] if p.parts else "")
            or "\x00" in name):
        raise AddonError("UNSAFE_ARCHIVE", "Архив содержит небезопасный путь.")
    target = (root / Path(*p.parts)).resolve()
    if root.resolve() != target and root.resolve() not in target.parents:
        raise AddonError("UNSAFE_ARCHIVE", "Архив пытается выйти за пределы каталога.")
    return target


def safe_unzip(data: bytes, dest: Path) -> list[str]:
    """Unpack a wheel into ``dest`` (created). Returns the list of top-level entries."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AddonError("UNSAFE_ARCHIVE", "Файл не является wheel-архивом.") from exc
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise AddonError("UNSAFE_ARCHIVE", "Архив слишком большой.")
    dest.mkdir(parents=True, exist_ok=True)
    plan: list[tuple[zipfile.ZipInfo, Path]] = []
    for info in infos:                                   # validate EVERYTHING before writing anything
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise AddonError("UNSAFE_ARCHIVE", "Архив содержит символическую ссылку.")
        plan.append((info, _safe_target(dest, info.filename)))
    tops: set[str] = set()
    for info, target in plan:
        tops.add(PurePosixPath(info.filename.replace("\\", "/")).parts[0])
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
    return sorted(tops)


def _promote(staging: Path, packages: Path) -> None:
    """Move unpacked top-level entries into ``packages``; ``*.data/{purelib,platlib}`` are flattened, scripts dropped."""
    packages.mkdir(parents=True, exist_ok=True)
    for entry in sorted(staging.iterdir()):
        if entry.name.endswith(".data"):
            for sub in ("purelib", "platlib"):
                d = entry / sub
                if d.is_dir():
                    for inner in d.iterdir():
                        _replace(inner, packages / inner.name)
            continue
        _replace(entry, packages / entry.name)


def _replace(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
    os.replace(src, dst)


# ------------------------------------------------------------------ state
def _load_state(root: Path) -> dict[str, Any]:
    try:
        raw = json.loads((root / STATE_FILE).read_text("utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(root: Path, state: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{STATE_FILE}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), "utf-8")
    os.replace(tmp, root / STATE_FILE)


def status(data_dir: Path | str, manifest: tuple[Artifact, ...] = MANIFEST) -> dict[str, Any]:
    """What is installed (from our own record; nothing imported, no network)."""
    state = _load_state(addon_root(data_dir))
    items = []
    for a in manifest:
        rec = state.get(a.name) or {}
        items.append({"name": a.name, "version": a.version, "pinned": a.pinned,
                      "installed": rec.get("sha256") == a.sha256 and a.pinned})
    return {"path": str(addon_path(data_dir)), "items": items,
            "complete": all(i["installed"] for i in items), "unpinned": [a.name for a in manifest if not a.pinned]}


# ------------------------------------------------------------------ install
def _result(status_: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status_, "ok": status_ == "PASS", "code": code, "message": message, **extra}


def install(data_dir: Path | str, *, downloader: Downloader | None = None,
            manifest: tuple[Artifact, ...] | None = None) -> dict[str, Any]:
    """Owner-triggered. All-or-nothing preflight, then per-artifact verify-before-unpack. Never raises."""
    manifest = MANIFEST if manifest is None else manifest
    dl = downloader or default_downloader
    root = addon_root(data_dir)
    unpinned = [a.name for a in manifest if not a.pinned]
    if unpinned:                                        # refuse BEFORE any network access
        return _result("BLOCKED", UNPINNED,
                       "Установка отклонена: для пакетов " + ", ".join(unpinned) + " нет проверенного sha256 "
                       "(UNPINNED_NEEDS_HASH). Непроверенные файлы не устанавливаются.",
                       unpinned=unpinned, packages=[], path=str(addon_path(data_dir)))
    state = _load_state(root)
    report: list[dict[str, Any]] = []
    try:
        for art in manifest:
            rec = state.get(art.name) or {}
            if rec.get("sha256") == art.sha256 and (addon_path(data_dir)).is_dir():
                report.append({"name": art.name, "version": art.version, "action": "already_installed"})
                continue
            url = _locate(art, dl)
            _check_url(url)
            blob = dl(url)
            if not isinstance(blob, (bytes, bytearray)) or len(blob) > MAX_DOWNLOAD_BYTES:
                raise AddonError("DOWNLOAD_FAILED", "Загрузка не удалась.", art.name)
            if hashlib.sha256(bytes(blob)).hexdigest() != art.sha256:
                raise AddonError("HASH_MISMATCH", f"sha256 файла {art.name} не совпал с закреплённым: файл отклонён, "
                                 "ничего не распаковано.", art.name)
            staging = root / f".staging-{uuid.uuid4().hex}"
            try:
                safe_unzip(bytes(blob), staging)
                _promote(staging, addon_path(data_dir))
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            state[art.name] = {"version": art.version, "sha256": art.sha256}
            _save_state(root, state)
            report.append({"name": art.name, "version": art.version, "action": "installed"})
    except AddonError as exc:
        return _result("BLOCKED", exc.code, exc.message, artifact=exc.artifact, packages=report,
                       path=str(addon_path(data_dir)))
    except Exception as exc:  # noqa: BLE001 - network / disk problems: report the class only, never a URL or path detail
        return _result("BLOCKED", "DOWNLOAD_FAILED", f"Установка не удалась ({type(exc).__name__}). Повторите вручную.",
                       packages=report, path=str(addon_path(data_dir)))
    if all(r["action"] == "already_installed" for r in report):
        return _result("PASS", "ALREADY_INSTALLED", "Зависимости звонков уже установлены.", packages=report,
                       path=str(addon_path(data_dir)))
    return _result("PASS", "INSTALLED", "Зависимости звонков установлены. Проверьте: bossman call doctor.",
                   packages=report, path=str(addon_path(data_dir)))
