"""Private Telegram block rule for Jeff (owner decision, kept outside Git).

The owner forbade Jeff from answering or messaging certain Telegram accounts.
Those IDs are personal data: they must never appear in Git, reports, logs or
test data. The rule therefore lives in a private text file outside every
checkout (default ``%LOCALAPPDATA%\\Bossman\\private\\jeff_blocked_telegram_ids.txt``,
override with the config key ``blocked_ids_file`` or the environment variable
``BOSSMAN_PIT_BLOCKED_IDS_FILE``), one numeric Telegram ID per line, ``#``
comments allowed.

On load every ID is immediately turned into the salted PIT person key
(HMAC-SHA256 with the private identity salt) and the raw number is dropped;
only the keyed hashes stay in memory. Status output reports a count, never an
ID or a hash.

Fail-closed rules:
- the file exists but cannot be read, or holds a line that is not a Telegram
  ID -> ``BlocklistError`` at start (Jeff refuses to start rather than run
  with a partially understood ban);
- an explicitly configured file that is missing -> ``BlocklistError``;
- the default file missing -> no ban (fresh install);
- a later edit that fails to parse keeps the last good rule (the ban never
  shrinks because of a typo) and is reported as ``reload_error``.

The block beats every allowlist: an open allowlist, a configured person entry
or the owner role do not bypass it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .identity import derive_person_key

BLOCKLIST_ENV = "BOSSMAN_PIT_BLOCKED_IDS_FILE"
DEFAULT_FILE_NAME = "jeff_blocked_telegram_ids.txt"
_ID_LINE = re.compile(r"^-?\d{1,20}$")


class BlocklistError(RuntimeError):
    """The private block rule cannot be trusted; the message never carries IDs."""


def default_blocklist_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "Bossman" / "private" / DEFAULT_FILE_NAME


def resolve_blocklist_path(configured: str = "") -> tuple[Path, bool]:
    """Return (path, required). An explicitly named file must exist."""
    env_value = os.environ.get(BLOCKLIST_ENV, "").strip()
    if env_value:
        return Path(env_value), True
    if str(configured or "").strip():
        return Path(str(configured).strip()), True
    return default_blocklist_path(), False


def _parse(text: str, salt: bytes) -> frozenset[str]:
    keys: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _ID_LINE.fullmatch(line):
            # The line number is safe to report; the content is not.
            raise BlocklistError(f"BLOCKLIST_INVALID_LINE:{number}")
        keys.add(derive_person_key(int(line), salt))
    return frozenset(keys)


class PrivateBlocklist:
    """In-memory set of salted keys for blocked Telegram accounts."""

    def __init__(self, path: Path | None, salt: bytes, *, required: bool = False):
        if len(salt) < 16:
            raise ValueError("identity salt must be at least 16 bytes")
        self.path = Path(path) if path is not None else None
        self._salt = bytes(salt)
        self.required = bool(required)
        self._keys: frozenset[str] = frozenset()
        self._stamp: tuple[int, int] | None = None
        self.reload_error = ""
        self._load(initial=True)

    @classmethod
    def from_settings(cls, settings) -> "PrivateBlocklist":
        path, required = resolve_blocklist_path(getattr(settings, "blocked_ids_file", ""))
        return cls(path, bytes.fromhex(settings.identity_salt), required=required)

    # -- loading -------------------------------------------------------------------------
    def _load(self, *, initial: bool) -> None:
        if self.path is None:
            return
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            if self.required and initial:
                raise BlocklistError("BLOCKLIST_FILE_MISSING") from None
            if not initial and self._keys:
                # A vanished file must not silently lift an active ban.
                self.reload_error = "BLOCKLIST_FILE_MISSING"
                return
            self._keys, self._stamp = frozenset(), None
            return
        except OSError:
            if initial:
                raise BlocklistError("BLOCKLIST_UNREADABLE") from None
            self.reload_error = "BLOCKLIST_UNREADABLE"
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return
        try:
            text = self.path.read_text(encoding="utf-8-sig")
            keys = _parse(text, self._salt)
        except BlocklistError as exc:
            if initial:
                raise
            self.reload_error = str(exc)
            return
        except (OSError, UnicodeDecodeError):
            if initial:
                raise BlocklistError("BLOCKLIST_UNREADABLE") from None
            self.reload_error = "BLOCKLIST_UNREADABLE"
            return
        self._keys, self._stamp, self.reload_error = keys, stamp, ""

    def refresh(self) -> None:
        """Pick up owner edits without a restart (cheap stat per call)."""
        self._load(initial=False)

    # -- decisions -----------------------------------------------------------------------
    def blocks_key(self, person_key: str) -> bool:
        return str(person_key) in self._keys

    def blocks_user(self, user_id, chat_id=None) -> bool:
        self.refresh()
        if not self._keys:
            return False
        for value in (user_id, chat_id):
            if value is None or isinstance(value, bool):
                continue
            try:
                if derive_person_key(int(value), self._salt) in self._keys:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def blocks_person(self, person) -> bool:
        return self.blocks_user(getattr(person, "user_id", None), getattr(person, "chat_id", None))

    def status(self) -> dict:
        """Count and health only: never an ID, a hash or the file content."""
        return {"source": "private_file" if self.path is not None else "none",
                "configured": self.path is not None and (self.required or self.path.exists()),
                "entries": len(self._keys),
                "reload_error": self.reload_error or None}
