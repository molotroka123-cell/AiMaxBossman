"""The owner's STOP — one source of truth for every plane of Bossman (immutable core, AGENTS.md §1).

Before 24.11.2026 at least eight modules read the STOP files on their own, with different rules:
some read only ``<data>/computer/STOP``, some also ``<data>/autonomy/STOP``; some failed OPEN on an unreadable
state; and the agent engine did not read STOP at all — with the global STOP set, a new pult/chat task was still
claimed, the model called and a tool EXECUTED (reproduced: tests/test_owner_stop_engine_gate.py). `stop --all`
killed the work in flight, but nothing kept Bossman stopped.

Rules here:
  * ``<data>/computer/STOP`` is the owner's GLOBAL STOP (palette, `bossman stop --all`, Telegram /stop). It stops
    every plane, the agent engine included.
  * ``<data>/autonomy/STOP`` is the autonomy-loop STOP. Planes that historically honour it too pass
    ``include_autonomy=True``; this module does not widen or narrow any plane's scope by itself.
  * Fail closed: a STOP path that cannot be checked (permission error, I/O error) counts as SET.
    "No data dir configured" is not an error: there is no STOP plane to read (bare unit engines).
  * Read-only. Setting/clearing stays where it is (tools_computer, autonomy.stop): this module never clears STOP.

No dependency on the rest of the package: every layer (engine, tools, features, CLI) may import it.
"""
from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

STOP_NAME = "STOP"
GLOBAL_DIR = "computer"
AUTONOMY_DIR = "autonomy"
_ABSENT = {errno.ENOENT, errno.ENOTDIR}


@dataclass(frozen=True)
class OwnerStop:
    active: bool
    sources: tuple = ()          # ("computer",) / ("autonomy",) / ("computer:unreadable",) ...

    @property
    def reason(self) -> str:
        return "" if not self.active else "owner STOP: " + ", ".join(self.sources)

    def __bool__(self) -> bool:  # `if owner_stop(...)` reads naturally
        return self.active


def global_stop_path(data_dir: str | os.PathLike) -> Path:
    return Path(data_dir) / GLOBAL_DIR / STOP_NAME


def autonomy_stop_path(data_dir: str | os.PathLike) -> Path:
    return Path(data_dir) / AUTONOMY_DIR / STOP_NAME


def _probe(path: Path) -> str | None:
    """'set' | 'unreadable' | None (absent)."""
    try:
        path.stat()
        return "set"
    except OSError as exc:
        if exc.errno in _ABSENT:
            return None
        return "unreadable"


def owner_stop(data_dir: str | os.PathLike | None, *, include_autonomy: bool = False) -> OwnerStop:
    if data_dir is None or str(data_dir) == "":
        return OwnerStop(False)
    checks = [(GLOBAL_DIR, global_stop_path(data_dir))]
    if include_autonomy:
        checks.append((AUTONOMY_DIR, autonomy_stop_path(data_dir)))
    found = []
    for name, path in checks:
        state = _probe(path)
        if state == "set":
            found.append(name)
        elif state == "unreadable":
            found.append(name + ":unreadable")
    return OwnerStop(bool(found), tuple(found))


def owner_stop_for(svc, *, include_autonomy: bool = False) -> OwnerStop:
    """Same, from a Services-like object. A svc that HAS settings but whose data_dir cannot be read fails closed."""
    if svc is None:
        return OwnerStop(False)
    try:
        settings = svc.settings
    except AttributeError:
        return OwnerStop(False)
    try:
        data_dir = settings.data_dir
    except Exception:  # noqa: BLE001 - settings exist but are broken: fail closed
        return OwnerStop(True, ("settings:unreadable",))
    return owner_stop(data_dir, include_autonomy=include_autonomy)


__all__ = ["OwnerStop", "owner_stop", "owner_stop_for", "global_stop_path", "autonomy_stop_path", "STOP_NAME"]
