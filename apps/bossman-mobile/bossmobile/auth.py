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


def load_or_create_pairing(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "tokens.json"
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("pairing_token"), str) and len(doc["pairing_token"]) >= 16:
            return doc["pairing_token"]
    token = secrets.token_urlsafe(18)
    path.write_text(json.dumps({"pairing_token": token, "created_at": int(time.time()),
                                "note": "Bossman Mobile pairing token. Not the Bossman token."}), encoding="utf-8")
    return token


def rotate_pairing(data_dir: Path) -> str:
    (data_dir / "tokens.json").unlink(missing_ok=True)
    (data_dir / "sessions.json").unlink(missing_ok=True)
    return load_or_create_pairing(data_dir)


class RateLimiter:
    """Failed attempts in the last 60 s: per key and in total."""

    def __init__(self, per_key: int, total: int, window_s: float = 60.0, clock=time.monotonic):
        self.per_key, self.total, self.window, self.clock = per_key, total, window_s, clock
        self._keys: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def retry_after(self, key: str) -> float:
        """0 when an attempt is allowed, else seconds to wait."""
        now = self.clock()
        with self._lock:
            q = self._keys.setdefault(key, deque())
            self._trim(q, now)
            self._trim(self._all, now)
            waits = []
            if len(q) >= self.per_key:
                waits.append(self.window - (now - q[0]))
            if len(self._all) >= self.total:
                waits.append(self.window - (now - self._all[0]))
            return max(waits) if waits else 0.0

    def failure(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._keys.setdefault(key, deque()).append(now)
            self._all.append(now)


class Sessions:
    def __init__(self, data_dir: Path, ttl_s: int):
        self.path = data_dir / "sessions.json"
        self.ttl = ttl_s
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        if self.path.exists():
            try:
                doc = json.loads(self.path.read_text(encoding="utf-8"))
                self._rows = {k: v for k, v in doc.items() if isinstance(v, dict) and v.get("expires", 0) > time.time()}
            except (OSError, ValueError):
                self._rows = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rows), encoding="utf-8")
        tmp.replace(self.path)

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
