"""Owner-controlled settings of the Telegram calls module (no secrets, ever).

Lives in the EXISTING Command Center data dir (``bcc.config.settings.data_dir``) under
``telegram_calls/settings.json`` and is written atomically. The file is deliberately dull:
switches, one allowed peer (numeric id + display label), limits and the last call outcome.
api_id / api_hash / session are NOT here - see ``account.credentials`` (encrypted in the Vault).

Fail-closed: a missing, unreadable or tampered file yields the defaults (calls OFF, no peer);
a file that lists more than ``MAX_ALLOWED_PEERS`` peers is treated as having no peer at all.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .types import MAX_ALLOWED_PEERS, PeerRef

DIR_NAME = "telegram_calls"
FILE_NAME = "settings.json"

MAX_CALL_S_RANGE = (30.0, 3600.0)
RING_TIMEOUT_S_RANGE = (10.0, 120.0)
#: fields the owner may change through ``update`` (peer + last outcome have their own methods)
PATCHABLE = frozenset({"enabled", "max_call_s", "ring_timeout_s", "record_audio", "keep_transcript"})
_OUTCOMES = frozenset({"completed", "declined", "busy", "no_answer", "connection_lost", "stopped",
                       "max_duration", "silence_timeout", "failed", "unknown"})


class SettingsError(ValueError):
    """Invalid settings patch (message is secret-free)."""


def calls_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / DIR_NAME


@dataclass(frozen=True)
class CallsSettings:
    enabled: bool = False
    allowed_peers: tuple[PeerRef, ...] = ()
    peer_confirmed: bool = False
    max_call_s: float = 900.0
    ring_timeout_s: float = 45.0
    record_audio: bool = False
    keep_transcript: bool = False
    last_outcome: str | None = None
    last_call_id: str | None = None
    last_call_at: float | None = None

    @property
    def peer(self) -> PeerRef | None:
        return self.allowed_peers[0] if self.allowed_peers else None

    def as_dict(self) -> dict[str, Any]:
        peer = self.peer
        return {"enabled": self.enabled,
                "peer": ({"user_id": peer.user_id, "label": peer.label} if peer else None),
                "peer_confirmed": bool(self.peer_confirmed and peer),
                "max_call_s": self.max_call_s, "ring_timeout_s": self.ring_timeout_s,
                "record_audio": self.record_audio, "keep_transcript": self.keep_transcript,
                "last_outcome": self.last_outcome, "last_call_id": self.last_call_id,
                "last_call_at": self.last_call_at}


def _clamp(value: Any, rng: tuple[float, float], name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{name}: number required")
    if not rng[0] <= float(value) <= rng[1]:
        raise SettingsError(f"{name}: must be within {rng[0]:g}..{rng[1]:g}")
    return float(value)


def _label(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    return text.strip()[:64]


def _from_dict(raw: Any) -> CallsSettings:
    """Tolerant, fail-closed parse: any doubt falls back to the safe default of that field."""
    if not isinstance(raw, dict):
        return CallsSettings()
    peers: list[PeerRef] = []
    for item in raw.get("allowed_peers") or []:
        try:
            peers.append(PeerRef(user_id=item["user_id"], label=_label(item.get("label"))))
        except (TypeError, ValueError, KeyError, AttributeError):
            peers = []
            break
    if len(peers) > MAX_ALLOWED_PEERS:      # tampered / hand-edited: trust none of them
        peers = []
    confirmed = raw.get("peer_confirmed") is True and bool(peers)

    def num(key: str, default: float, rng: tuple[float, float]) -> float:
        try:
            return _clamp(raw.get(key, default), rng, key)
        except SettingsError:
            return default

    outcome = raw.get("last_outcome")
    at = raw.get("last_call_at")
    call_id = raw.get("last_call_id")
    return CallsSettings(
        enabled=raw.get("enabled") is True, allowed_peers=tuple(peers), peer_confirmed=confirmed,
        max_call_s=num("max_call_s", 900.0, MAX_CALL_S_RANGE),
        ring_timeout_s=num("ring_timeout_s", 45.0, RING_TIMEOUT_S_RANGE),
        record_audio=raw.get("record_audio") is True, keep_transcript=raw.get("keep_transcript") is True,
        last_outcome=outcome if outcome in _OUTCOMES else None,
        last_call_id=call_id if isinstance(call_id, str) and len(call_id) <= 128 else None,
        last_call_at=float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None)


def _to_dict(s: CallsSettings) -> dict[str, Any]:
    return {"version": 1, "enabled": s.enabled,
            "allowed_peers": [{"user_id": p.user_id, "label": p.label} for p in s.allowed_peers],
            "peer_confirmed": s.peer_confirmed, "max_call_s": s.max_call_s, "ring_timeout_s": s.ring_timeout_s,
            "record_audio": s.record_audio, "keep_transcript": s.keep_transcript,
            "last_outcome": s.last_outcome, "last_call_id": s.last_call_id, "last_call_at": s.last_call_at}


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class SettingsStore:
    """Read-modify-write with one lock per process; a fresh disk read on every ``load``
    (the worker re-reads at dial time, so a stale copy can never authorise a call)."""

    def __init__(self, data_dir: Path | str):
        self.path = calls_dir(data_dir) / FILE_NAME
        self._lock = threading.RLock()

    def load(self) -> CallsSettings:
        try:
            return _from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return CallsSettings()

    def _save(self, s: CallsSettings) -> CallsSettings:
        atomic_write_json(self.path, _to_dict(s))
        return s

    def update(self, patch: dict[str, Any]) -> CallsSettings:
        if not isinstance(patch, dict):
            raise SettingsError("object required")
        unknown = set(patch) - PATCHABLE
        if unknown:
            raise SettingsError("unknown or read-only setting: " + ", ".join(sorted(map(str, unknown))))
        changes: dict[str, Any] = {}
        for key, value in patch.items():
            if key in ("enabled", "record_audio", "keep_transcript"):
                if not isinstance(value, bool):
                    raise SettingsError(f"{key}: true/false required")
                changes[key] = value
            elif key == "max_call_s":
                changes[key] = _clamp(value, MAX_CALL_S_RANGE, key)
            else:
                changes[key] = _clamp(value, RING_TIMEOUT_S_RANGE, key)
        with self._lock:
            return self._save(replace(self.load(), **changes))

    def set_peer(self, user_id: int, label: str = "") -> CallsSettings:
        """Choose THE allowed peer (replaces any previous one). Never confirmed automatically."""
        try:
            peer = PeerRef(user_id=user_id, label=_label(label))     # validates the id
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        with self._lock:
            return self._save(replace(self.load(), allowed_peers=(peer,)[:MAX_ALLOWED_PEERS], peer_confirmed=False))

    def confirm_peer(self, user_id: int) -> CallsSettings:
        with self._lock:
            cur = self.load()
            if cur.peer is None or cur.peer.user_id != user_id:
                raise SettingsError("confirm names a different peer than the selected one")
            return self._save(replace(cur, peer_confirmed=True))

    def clear_peer(self) -> CallsSettings:
        with self._lock:
            return self._save(replace(self.load(), allowed_peers=(), peer_confirmed=False))

    def record_outcome(self, outcome: str | None, call_id: str | None = None,
                       at: float | None = None) -> CallsSettings:
        if outcome is not None and outcome not in _OUTCOMES:
            raise SettingsError("unknown outcome")
        with self._lock:
            return self._save(replace(self.load(), last_outcome=outcome, last_call_id=call_id,
                                      last_call_at=time.time() if at is None else at))
