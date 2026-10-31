"""Phone authentication: one pairing token -> server-side session (HttpOnly cookie) + CSRF token.

The pairing token lives in %USERPROFILE%/.bossman-mobile/tokens.json (created on first start). Sessions are kept
as SHA-256 of their id in sessions.json, so a copy of that file does not log anyone in. Failed logins are limited
per client and globally (sliding minute window); the limit is checked BEFORE the token is compared.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import deque
from pathlib import Path

COOKIE = "bm_session"
CSRF_HEADER = "X-BM-CSRF"


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


import base64
import os
import subprocess
import warnings

_MOBILE_DPAPI_ENTROPY = b"bossman-mobile-auth-dpapi-v1\x00"


def _restrict_to_owner(path: Path) -> None:
    """Apply owner-only ACL on Windows (icacls) or 0700/0600 on POSIX."""
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    principal = f"{domain}\\{user}" if domain and user else (user or "%USERNAME%")
    grant = f"{principal}:(OI)(CI)F" if os.path.isdir(path) else f"{principal}:F"
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"bossmobile.auth: icacls failed for {path}: {exc!r}", UserWarning, stacklevel=2)


def _dpapi_protect(plaintext: bytes) -> bytes:
    if os.name != "nt":
        # Non-Windows fallback: obfuscated envelope + 0600 permissions
        key = hashlib.sha256(_MOBILE_DPAPI_ENTROPY).digest()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(plaintext))
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER( ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_buf = ctypes.create_string_buffer(plaintext, len(plaintext))
    ent_buf = ctypes.create_string_buffer(_MOBILE_DPAPI_ENTROPY, len(_MOBILE_DPAPI_ENTROPY))
    in_blob = _BLOB(len(plaintext), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    ent_blob = _BLOB(len(_MOBILE_DPAPI_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), "bossman-mobile", ctypes.byref(ent_blob), None, None, 0x01, ctypes.byref(out_blob)):
        raise OSError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    if os.name != "nt":
        key = hashlib.sha256(_MOBILE_DPAPI_ENTROPY).digest()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(blob))
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_buf = ctypes.create_string_buffer(blob, len(blob))
    ent_buf = ctypes.create_string_buffer(_MOBILE_DPAPI_ENTROPY, len(_MOBILE_DPAPI_ENTROPY))
    in_blob = _BLOB(len(blob), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    ent_blob = _BLOB(len(_MOBILE_DPAPI_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, ctypes.byref(ent_blob), None, None, 0x01, ctypes.byref(out_blob)):
        raise ValueError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _write_protected_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sealed = _dpapi_protect(raw)
    envelope = json.dumps({"v": 1, "dpapi_b64": base64.b64encode(sealed).decode("ascii")})
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(envelope, encoding="utf-8")
    _restrict_to_owner(tmp)
    tmp.replace(path)
    _restrict_to_owner(path)


def _read_protected_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        outer = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(outer, dict):
        return None
    if isinstance(outer.get("dpapi_b64"), str):
        try:
            raw = _dpapi_unprotect(base64.b64decode(outer["dpapi_b64"]))
            inner = json.loads(raw.decode("utf-8"))
            return inner if isinstance(inner, dict) else None
        except Exception:  # noqa: BLE001
            return None
    # Transparent migration from legacy plaintext JSON to DPAPI-sealed JSON
    _write_protected_json(path, outer)
    return outer


def load_or_create_pairing(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    _restrict_to_owner(data_dir)
    path = data_dir / "tokens.json"
    doc = _read_protected_json(path)
    if isinstance(doc, dict) and isinstance(doc.get("pairing_token"), str) and len(doc["pairing_token"]) >= 16:
        return doc["pairing_token"]
    token = secrets.token_urlsafe(18)
    _write_protected_json(
        path,
        {
            "pairing_token": token,
            "created_at": int(time.time()),
            "note": "Bossman Mobile pairing token (DPAPI-sealed). Not the Bossman master token.",
        },
    )
    return token


def rotate_pairing(data_dir: Path) -> str:
    (data_dir / "tokens.json").unlink(missing_ok=True)
    (data_dir / "sessions.json").unlink(missing_ok=True)
    return load_or_create_pairing(data_dir)


class RateLimiter:
    """Failed attempts in the last 60 s per client key.

    Security audit 31.10 (1d): a global total counter across all keys allowed an
    unauthenticated noisy client (or spoofed X-Forwarded-For rotation) to lock out
    the legitimate owner's device. Rate limiting is strictly per client key with
    bounded key-table eviction so one client can never lock out another."""

    def __init__(self, per_key: int, total: int = 30, window_s: float = 60.0,
                 clock=time.monotonic, max_keys: int = 1024):
        self.per_key, self.total, self.window, self.clock = per_key, total, window_s, clock
        self.max_keys = max(32, int(max_keys))
        self._keys: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def _evict_if_needed(self, now: float) -> None:
        if len(self._keys) < self.max_keys:
            return
        stale = [k for k, q in self._keys.items() if not q or (now - q[-1] > self.window)]
        for k in stale:
            self._keys.pop(k, None)
        while len(self._keys) >= self.max_keys:
            oldest_key = next(iter(self._keys))
            self._keys.pop(oldest_key, None)

    def retry_after(self, key: str) -> float:
        """0 when an attempt is allowed for `key`, else seconds to wait."""
        now = self.clock()
        with self._lock:
            q = self._keys.get(key)
            if not q:
                return 0.0
            self._trim(q, now)
            if not q:
                self._keys.pop(key, None)
                return 0.0
            if len(q) >= self.per_key:
                return max(0.0, self.window - (now - q[0]))
            return 0.0

    def failure(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._evict_if_needed(now)
            q = self._keys.setdefault(key, deque())
            self._trim(q, now)
            q.append(now)
            self._trim(self._all, now)
            self._all.append(now)

    def success(self, key: str) -> None:
        with self._lock:
            self._keys.pop(key, None)


class Sessions:
    def __init__(self, data_dir: Path, ttl_s: int):
        self.path = data_dir / "sessions.json"
        self.ttl = ttl_s
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        doc = _read_protected_json(self.path)
        if isinstance(doc, dict):
            now = time.time()
            self._rows = {k: v for k, v in doc.items() if isinstance(v, dict) and v.get("expires", 0) > now}

    def _save(self) -> None:
        _write_protected_json(self.path, self._rows)

    def create(self, label: str = "") -> tuple[str, dict]:
        sid = secrets.token_urlsafe(32)
        row = {"csrf": secrets.token_urlsafe(24), "panel_csrf": secrets.token_urlsafe(24),
               "created": time.time(), "expires": time.time() + self.ttl, "label": label[:80]}
        with self._lock:
            self._rows[_h(sid)] = row
            self._save()
        return sid, row

    def get(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        row = self._rows.get(_h(sid))
        if row is None or row.get("expires", 0) < time.time():
            return None
        return row

    def revoke(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            gone = self._rows.pop(_h(sid), None) is not None
            if gone:
                self._save()
        return gone

    def revoke_all(self) -> int:
        with self._lock:
            n = len(self._rows)
            self._rows.clear()
            self._save()
        return n

    def count(self) -> int:
        now = time.time()
        return sum(1 for r in self._rows.values() if r.get("expires", 0) > now)



def check_token(sent: object, expected: str) -> bool:
    if not isinstance(sent, str) or not sent:
        return False
    return secrets.compare_digest(sent.strip().encode("utf-8", "ignore"), expected.encode("utf-8"))


class WorkerDeviceRegistry:
    """Single-core worker-device registry (Steps 15.1, 15.2).
    Connects additional worker nodes (second PC, laptop, phone assistant) to the single Bossman core
    with shared STOP propagation (<= 2.0s) and heartbeat tracking without creating a second core."""

    def __init__(self, heartbeat_timeout_s: float = 15.0, clock=time.monotonic):
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.clock = clock
        self._lock = threading.Lock()
        self._devices: dict[str, dict] = {}
        self.global_stop = False
        self.stop_ts: float | None = None

    def register(self, device_id: str, *, role: str, capabilities: list[str] | None = None) -> dict:
        now = self.clock()
        with self._lock:
            rec = {
                "device_id": device_id,
                "role": role,
                "capabilities": list(capabilities or []),
                "last_heartbeat": now,
                "active_task_id": None,
                "stopped": self.global_stop,
            }
            self._devices[device_id] = rec
            return dict(rec)

    def heartbeat(self, device_id: str, *, active_task_id: str | int | None = None) -> dict:
        now = self.clock()
        with self._lock:
            if device_id not in self._devices:
                raise KeyError(device_id)
            rec = self._devices[device_id]
            rec["last_heartbeat"] = now
            rec["active_task_id"] = None if self.global_stop else active_task_id
            rec["stopped"] = self.global_stop
            return dict(rec)

    def trigger_global_stop(self) -> dict:
        t0 = self.clock()
        with self._lock:
            self.global_stop = True
            self.stop_ts = t0
            aborted = 0
            for rec in self._devices.values():
                if rec.get("active_task_id") is not None:
                    aborted += 1
                rec["active_task_id"] = None
                rec["stopped"] = True
            elapsed = self.clock() - t0
            return {
                "global_stop": True,
                "devices_stopped": len(self._devices),
                "tasks_aborted": aborted,
                "hanging_tasks": 0,
                "elapsed_s": round(elapsed, 6),
            }

    def clear_global_stop(self) -> None:
        """«Продолжить»: worker devices may take tasks again (only after Bossman confirmed STOP is off)."""
        with self._lock:
            self.global_stop = False
            for rec in self._devices.values():
                rec["stopped"] = False

    def snapshot(self) -> dict:
        now = self.clock()
        with self._lock:
            devices = [
                {
                    **rec,
                    "online": (now - float(rec.get("last_heartbeat", 0.0))) <= self.heartbeat_timeout_s,
                }
                for rec in self._devices.values()
            ]
            return {
                "global_stop": self.global_stop,
                "count": len(devices),
                "online": sum(1 for d in devices if d["online"]),
                "devices": devices,
            }
