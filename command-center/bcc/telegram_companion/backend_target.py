"""Which Command Center may receive the owner token: the one serving the data root.

Companion and Jeff configurations were saved with ``core_url =
http://127.0.0.1:8800`` long before a release candidate ran on another port
(8820). Trusting that address sent the RC's X-BCC-Token — and the owner's
approval decisions — to whatever listened on 8800.

The serving backend of a data root is published by ``bcc.backend_lock``
(``backend.lock`` held + ``backend.json`` with port and build). Before any
token leaves this process the target is checked on the PUBLIC
``/api/identity`` endpoint (no token): it must be Command Center and, when the
lock holder names its build, exactly that build.
"""
from __future__ import annotations

import time
from pathlib import Path

from .config import CompanionError

IDENTITY_TTL_SECONDS = 30.0


def default_core_data_dir() -> Path | None:
    """The data root this code's Command Center uses (same env as the backend)."""
    try:
        from bcc.config import _data_dir
        return Path(_data_dir())
    except Exception:  # noqa: BLE001 — discovery is best effort
        return None


def discover(configured_url: str, data_dir: str | Path | None, *,
             strict: bool = False) -> tuple[str, str | None]:
    """(base URL, expected build SHA or None).

    The lock holder of ``data_dir`` wins over the configured URL. ``strict``
    (the data root was saved explicitly) refuses when nobody serves it: a
    process on the configured port then belongs to another data root.
    """
    holder = None
    if data_dir:
        try:
            from bcc.backend_lock import running_backend
            holder = running_backend(Path(data_dir))
        except (OSError, ValueError):
            holder = None
    port = holder.get("port") if isinstance(holder, dict) else None
    if type(port) is int and 0 < port < 65536:
        sha = holder.get("build_sha")
        return f"http://127.0.0.1:{port}", (sha if isinstance(sha, str) and sha else None)
    if strict:
        raise CompanionError("CORE_NOT_RUNNING")
    return configured_url.rstrip("/"), None


class VerifiedBackend:
    """Resolves and identity-checks the Command Center before the token is used."""

    def __init__(self, configured_url: str, data_dir: str | Path | None = None, *,
                 strict: bool = False, clock=time.monotonic):
        self.configured_url = configured_url
        self.data_dir = data_dir if data_dir else default_core_data_dir()
        self.strict = strict and bool(data_dir)
        self._clock = clock
        self._verified: tuple[str, float] | None = None

    def forget(self) -> None:
        self._verified = None

    async def base_url(self, client) -> str:
        from .adapters import json_request
        now = self._clock()
        if self._verified and now - self._verified[1] < IDENTITY_TTL_SECONDS:
            return self._verified[0]
        url, expected_sha = discover(self.configured_url, self.data_dir, strict=self.strict)
        ident = await json_request(client, "GET", url + "/api/identity", timeout=5)   # no token
        from bcc.build_identity import DESKTOP_APP_IDENTITY
        if (not isinstance(ident, dict) or ident.get("app") != DESKTOP_APP_IDENTITY
                or (expected_sha and ident.get("build_sha") != expected_sha)):
            self._verified = None
            raise CompanionError("CORE_IDENTITY_MISMATCH")
        self._verified = (url, now)
        return url
