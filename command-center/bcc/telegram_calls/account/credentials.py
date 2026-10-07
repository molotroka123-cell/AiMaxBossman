"""Encrypted storage of the MTProto identity: api_id, api_hash, session string.

* Everything is in ONE Vault-encrypted file (``credentials.enc``), never in ``config.json``, Git, logs, events,
  API responses or model prompts. Only masks leave this module (``public()``).
* The Vault is Bossman's own (``bcc.secrets.Vault`` over the data dir: ``secret.key`` / ``BOSSMAN_VAULT_KEY``), the one
  that already protects provider keys: the existing protected storage, not a new one. The file gets the owner-only ACL.
* ``Credentials.__repr__`` never prints a secret.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ...secrets import Vault, mask
from ..settings import calls_home, secret_home
from ..types import CallError

FILE = "credentials.enc"
_API_HASH = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass
class Credentials:
    api_id: int = 0
    api_hash: str = field(default="", repr=False)
    session: str = field(default="", repr=False)
    me_id: int = 0
    phone_last4: str = ""
    phone: str = field(default="", repr=False)   # full login phone, vault-encrypted, never logged

    def __repr__(self) -> str:                       # explicit: no secret can reach a log through repr()
        return f"Credentials(api_id=…{str(self.api_id)[-2:]}, session={'set' if self.session else 'none'})"

    @property
    def has_api(self) -> bool:
        return bool(self.api_id and self.api_hash)


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"+••••{digits[-4:]}" if len(digits) >= 4 else ""


class CredentialStore:
    def __init__(self, home: Path | None = None, *, vault: Vault | None = None):
        self.home = home or calls_home()
        self._vault = vault
        self.path = self.home / FILE

    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = Vault(secret_home())
        return self._vault

    # ------------------------------------------------------------ read
    def load(self) -> Credentials:
        if not self.path.is_file():
            return Credentials()
        value = self.vault.decrypt(self.path.read_text(encoding="utf-8"))
        if value is None:
            raise CallError("NOT_LOGGED_IN", detail="credentials_undecryptable")
        data = json.loads(value)
        return Credentials(api_id=int(data.get("api_id") or 0), api_hash=str(data.get("api_hash") or ""),
                           session=str(data.get("session") or ""), me_id=int(data.get("me_id") or 0),
                           phone_last4=str(data.get("phone_last4") or ""),
                           phone=str(data.get("phone") or ""))

    def public(self) -> dict:
        try:
            c = self.load()
        except CallError:
            return {"has_api": False, "has_session": False, "unreadable": True}
        return {"has_api": c.has_api, "api_id": mask(str(c.api_id)) if c.api_id else None,
                "has_session": bool(c.session), "phone": (mask_phone(c.phone) if c.phone else (f"+••••{c.phone_last4}" if c.phone_last4 else None)),
                "me_id": c.me_id or None, "unreadable": False}

    # ------------------------------------------------------------ write
    def save_api(self, api_id: int, api_hash: str) -> None:
        if type(api_id) is not int or not 0 < api_id < 2**31:
            raise CallError("NO_CREDENTIALS", detail="api_id_invalid")
        if not isinstance(api_hash, str) or not _API_HASH.match(api_hash.strip()):
            raise CallError("NO_CREDENTIALS", detail="api_hash_invalid")
        cur = self._load_or_empty()
        changed = (cur.api_id, cur.api_hash) != (api_id, api_hash.strip())
        cur.api_id, cur.api_hash = api_id, api_hash.strip()
        if changed:                                   # a session belongs to the api_id it was created with
            cur.session, cur.me_id, cur.phone_last4 = "", 0, ""
        self._write(cur)

    def save_phone(self, phone: str) -> None:
        import re as _re
        digits = _re.sub(r"\D", "", phone or "")
        if not (7 <= len(digits) <= 15):
            raise CallError("LOGIN_PHONE_INVALID")
        cur = self._load_or_empty()
        cur.phone = "+" + digits
        cur.phone_last4 = digits[-4:]
        self._write(cur)

    def saved_phone(self) -> str:
        try:
            return self._load_or_empty().phone
        except CallError:
            return ""

    def save_session(self, session: str, me_id: int, phone: str) -> None:
        cur = self._load_or_empty()
        cur.session, cur.me_id = session, int(me_id)
        cur.phone_last4 = re.sub(r"\D", "", phone or "")[-4:]
        self._write(cur)

    def clear_session(self) -> None:
        cur = self._load_or_empty()
        cur.session, cur.me_id, cur.phone_last4 = "", 0, ""
        self._write(cur)

    def clear_all(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------ internals
    def _load_or_empty(self) -> Credentials:
        try:
            return self.load()
        except CallError:
            return Credentials()

    def _write(self, c: Credentials) -> None:
        from ..hardening import restrict_to_owner
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        restrict_to_owner(self.home)     # the directory variant: config/state/history already inside stay readable
        blob = self.vault.encrypt(json.dumps({"api_id": c.api_id, "api_hash": c.api_hash, "session": c.session,
                                              "me_id": c.me_id, "phone_last4": c.phone_last4,
                                              "phone": c.phone}))
        restrict_to_owner(self.vault.path)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(blob)
            out.flush()
            os.fsync(out.fileno())
        restrict_to_owner(tmp)
        os.replace(tmp, self.path)
