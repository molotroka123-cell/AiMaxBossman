"""keys_guard: provider API keys must not be lost (owner order, 07.10).

The live keys file is %LOCALAPPDATA%\\Bossman\\keys\\provider-keys.env (read first by
coding_tasks). This tool keeps a DPAPI-protected copy of it (current Windows user only) next to
it plus rotating timestamped copies in %LOCALAPPDATA%\\Bossman\\backups\\keys (keep 5).

    python tools/keys_guard.py backup | restore | verify | ensure

Rules: key VALUES never reach stdout/stderr/logs (names only); env and copy are merged as a
union where a non-empty env value wins; a corrupt protected copy is never overwritten; every
write is atomic. DPAPI exists only on Windows: elsewhere the protected operations refuse.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


class KeysGuardError(Exception):
    """Base exception for keys_guard errors."""


class _Blob(ctypes.Structure):
    _fields_ = [
        ('cbData', ctypes.c_uint32),
        ('pbData', ctypes.POINTER(ctypes.c_char))
    ]


def dpapi_protect(data: bytes) -> bytes:
    """Protect data using DPAPI CryptProtectData."""
    if not data:
        return b''

    # Prepare input blob
    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    # Output blob
    out_blob = _Blob()
    # CryptProtectData parameters: pDataIn, szDataDescr, pOptionalEntropy,
    # pvReserved, pPromptStruct, dwFlags, pDataOut
    ret = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,  # szDataDescr
        None,  # pOptionalEntropy
        None,  # pvReserved
        None,  # pPromptStruct
        0,     # dwFlags
        ctypes.byref(out_blob)
    )
    if ret == 0:
        raise KeysGuardError("DPAPI protect failed")

    try:
        # Copy protected data
        protected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return protected
    finally:
        # Free memory allocated by CryptProtectData
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(blob: bytes) -> bytes:
    """Unprotect data using DPAPI CryptUnprotectData."""
    if not blob:
        return b''

    # Prepare input blob
    buf = ctypes.create_string_buffer(blob, len(blob))
    in_blob = _Blob(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    # Output blob
    out_blob = _Blob()
    ret = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,  # ppszDataDescr
        None,  # pOptionalEntropy
        None,  # pvReserved
        None,  # pPromptStruct
        0,     # dwFlags
        ctypes.byref(out_blob)
    )
    if ret == 0:
        raise KeysGuardError("DPAPI unprotect failed")

    try:
        # Copy unprotected data
        unprotected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return unprotected
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to path atomically using a temporary file."""
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create a temporary file in the same directory
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
    try:
        with os.fdopen(fd, 'wb') as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        # Replace the target file with the temporary file
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temporary file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- env and merge

ENV_NAME = "provider-keys.env"
BLOB_NAME = "provider-keys.dpapi"
KEEP = 5


def default_keys_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Bossman" / "keys"


def default_backup_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Bossman" / "backups" / "keys"


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments are skipped; the last duplicate wins."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if name:
            out[name] = value.strip()
    return out


def serialize_env(keys: dict[str, str]) -> str:
    return "".join(f"{k}={keys[k]}\n" for k in sorted(keys))


def merge(env: dict[str, str], saved: dict[str, str]) -> dict[str, str]:
    """Union of names. The env value wins only when it is non-empty."""
    out = dict(saved)
    for k, v in env.items():
        if v or k not in out:
            out[k] = v
    return out


# ---------------------------------------------------------------- protected copy

def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise KeysGuardError("DPAPI is available only on Windows")
    return dpapi_protect(data)


def _unprotect(blob: bytes) -> bytes:
    if os.name != "nt":
        raise KeysGuardError("DPAPI is available only on Windows")
    return dpapi_unprotect(blob)


def _read_blob(path: Path) -> dict[str, str]:
    """Decode one protected copy; any problem is KeysGuardError (never a partial dict)."""
    try:
        data = json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
    except KeysGuardError:
        raise
    except Exception as exc:  # noqa: BLE001 — torn file, wrong user, not JSON
        raise KeysGuardError(f"{path.name}: unreadable protected copy ({type(exc).__name__})") from None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise KeysGuardError(f"{path.name}: protected copy is not a name->value map")
    return data


def _copies(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("provider-keys-*.dpapi"), reverse=True)


def load_saved(keys_dir: Path, backup_dir: Path) -> tuple[dict[str, str], str]:
    """The newest readable protected copy: primary first, then timestamped copies."""
    for path in [keys_dir / BLOB_NAME, *_copies(backup_dir)]:
        if path.is_file():
            try:
                return _read_blob(path), path.name
            except KeysGuardError:
                continue
    return {}, ""


def _read_env(keys_dir: Path) -> dict[str, str]:
    path = keys_dir / ENV_NAME
    return parse_env(path.read_text(encoding="utf-8-sig", errors="replace")) if path.is_file() else {}


def _owner_only(path: Path) -> None:
    """Best effort: on Windows restrict the env file to the current user."""
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME")
    if user:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                       capture_output=True, check=False)


def backup(keys_dir: Path, backup_dir: Path, *, now: float | None = None) -> dict:
    env = _read_env(keys_dir)
    if not env:
        return {"ok": False, "reason": f"{ENV_NAME} is missing or empty: nothing to protect", "names": []}
    saved, _ = load_saved(keys_dir, backup_dir)
    keys = merge(env, saved)
    payload = _protect(json.dumps(keys, sort_keys=True).encode("utf-8"))
    primary = keys_dir / BLOB_NAME
    primary_corrupt = False
    if primary.is_file():
        try:
            _read_blob(primary)
        except KeysGuardError:
            primary_corrupt = True          # never overwrite it: it may be the only evidence
    if not primary_corrupt:
        atomic_write_bytes(primary, payload)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    copy = backup_dir / f"provider-keys-{stamp}.dpapi"
    n = 1
    while copy.exists():
        n += 1
        copy = backup_dir / f"provider-keys-{stamp}-{n}.dpapi"
    atomic_write_bytes(copy, payload)
    for old in _copies(backup_dir)[KEEP:]:
        with contextlib.suppress(OSError):
            old.unlink()
    return {"ok": True, "names": sorted(keys), "primary_written": not primary_corrupt,
            "primary_corrupt_kept": primary_corrupt, "copy": copy.name}


def restore(keys_dir: Path, backup_dir: Path) -> dict:
    saved, source = load_saved(keys_dir, backup_dir)
    if not saved:
        return {"ok": False, "reason": "no readable protected copy", "names": []}
    env = _read_env(keys_dir)
    keys = merge(env, saved)
    restored = sorted(k for k in keys if not env.get(k))
    if restored or not (keys_dir / ENV_NAME).is_file():
        path = keys_dir / ENV_NAME
        atomic_write_bytes(path, serialize_env(keys).encode("utf-8"))
        _owner_only(path)
    return {"ok": True, "source": source, "names": sorted(keys), "restored": restored}


def verify(keys_dir: Path, backup_dir: Path) -> dict:
    env = _read_env(keys_dir)
    saved, source = load_saved(keys_dir, backup_dir)
    present = sorted(k for k, v in env.items() if v)
    missing = sorted(k for k, v in saved.items() if v and not env.get(k))
    if not source:
        code, verdict = 2, "NO_PROTECTED_COPY"
    elif missing:
        code, verdict = 1, "KEYS_MISSING_IN_ENV"
    else:
        code, verdict = 0, "OK"
    return {"ok": code == 0, "code": code, "verdict": verdict, "env_names": present,
            "saved_names": sorted(saved), "missing": missing, "source": source}


def ensure(keys_dir: Path, backup_dir: Path) -> dict:
    """Bring missing keys back from the protected copy, then refresh the copy."""
    out: dict = {"restore": None, "backup": None}
    v = verify(keys_dir, backup_dir)
    if v["code"] == 1 or (v["source"] and not (keys_dir / ENV_NAME).is_file()):
        out["restore"] = restore(keys_dir, backup_dir)
    out["backup"] = backup(keys_dir, backup_dir)
    out["verify"] = verify(keys_dir, backup_dir)
    out["ok"] = out["verify"]["code"] == 0
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Protect provider API keys (names only in output).")
    ap.add_argument("action", choices=["backup", "restore", "verify", "ensure"])
    ap.add_argument("--keys-dir", type=Path, default=None)
    ap.add_argument("--backup-dir", type=Path, default=None)
    a = ap.parse_args(argv)
    keys_dir = a.keys_dir or default_keys_dir()
    backup_dir = a.backup_dir or default_backup_dir()
    try:
        result = {"backup": backup, "restore": restore, "verify": verify, "ensure": ensure}[a.action](keys_dir, backup_dir)
    except KeysGuardError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    if a.action == "verify":
        return int(result["code"])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
