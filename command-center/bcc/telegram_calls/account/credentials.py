"""api_id / api_hash / session string, encrypted at rest with the EXISTING ``bcc.secrets.Vault``.

One file, ``<data_dir>/telegram_calls/credentials.enc``: a single Fernet blob (Vault key file 0600 or
``BOSSMAN_VAULT_KEY``) that is additionally restricted to the owner with ``bcc.auth._restrict_to_owner``.
Nothing secret is ever returned by ``status`` (booleans only), logged, or put in an exception.
``get()`` is for the worker process only; it returns a dataclass whose repr hides the values.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..settings import calls_dir
from ..types import CallError

FILE_NAME = "credentials.enc"
_API_HASH = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass(frozen=True)
class Credentials:
    api_id: int = field(repr=False)
    api_hash: str = field(repr=False)
    session: str = field(default="", repr=False)
    self_id: int | None = field(default=None, repr=False)


def _restrict(path: Path) -> None:
    from ...auth import _restrict_to_owner     # existing helper (icacls on Windows, no-op on POSIX)
    _restrict_to_owner(path)


class CredentialStore:
    def __init__(self, data_dir: Path | str, vault: Any = None):
        self.data_dir = Path(data_dir)
        self.path = calls_dir(self.data_dir) / FILE_NAME
        self._vault = vault
        self._lock = threading.RLock()

    @property
    def vault(self) -> Any:
        if self._vault is None:
            from ...secrets import Vault
            self._vault = Vault(self.data_dir)
        return self._vault

    # ---------------------------------------------------------------- raw blob
    def _read(self) -> dict[str, Any]:
        try:
            blob = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        plain = self.vault.decrypt(blob.strip())
        if not plain:
            return {}
        try:
            data = json.loads(plain)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{FILE_NAME}.{os.getpid()}.tmp")
        tmp.write_text(self.vault.encrypt(json.dumps(data)) or "", encoding="utf-8")
        _restrict(tmp)                      # restrict BEFORE the swap: no readable window
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- API
    def save_api(self, api_id: Any, api_hash: Any) -> None:
        """Save api_id/api_hash. A changed api pair invalidates the stored session."""
        if isinstance(api_id, bool) or not isinstance(api_id, int) or not 0 < api_id < 2**31:
            raise CallError("NO_CREDENTIALS", detail="api_id_invalid")
        if not isinstance(api_hash, str) or not _API_HASH.match(api_hash.strip()):
            raise CallError("NO_CREDENTIALS", detail="api_hash_invalid")
        with self._lock:
            data = self._read()
            if data.get("api_id") != api_id or data.get("api_hash") != api_hash.strip():
                data.pop("session", None)
                data.pop("self_id", None)
            data["api_id"], data["api_hash"] = api_id, api_hash.strip()
            self._write(data)

    def save_session(self, session: str, self_id: int) -> None:
        if not isinstance(session, str) or not session or isinstance(self_id, bool) or not isinstance(self_id, int):
            raise CallError("NOT_LOGGED_IN", detail="session_invalid")
        with self._lock:
            data = self._read()
            if "api_id" not in data:
                raise CallError("NO_CREDENTIALS")
            data["session"], data["self_id"] = session, self_id
            self._write(data)

    def clear_session(self) -> None:
        with self._lock:
            data = self._read()
            if data:
                data.pop("session", None)
                data.pop("self_id", None)
                self._write(data)

    def clear_all(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

    def has_api(self) -> bool:
        d = self._read()
        return bool(d.get("api_id") and d.get("api_hash"))

    def has_session(self) -> bool:
        d = self._read()
        return bool(d.get("api_id") and d.get("api_hash") and d.get("session"))

    def self_id(self) -> int | None:
        value = self._read().get("self_id")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def status(self) -> dict[str, bool]:
        """Booleans only - safe for API responses and logs."""
        return {"has_credentials": self.has_api(), "has_session": self.has_session()}

    def get(self) -> Credentials:
        d = self._read()
        if not (d.get("api_id") and d.get("api_hash")):
            raise CallError("NO_CREDENTIALS")
        return Credentials(api_id=int(d["api_id"]), api_hash=str(d["api_hash"]), session=str(d.get("session") or ""),
                           self_id=d.get("self_id") if isinstance(d.get("self_id"), int) else None)
