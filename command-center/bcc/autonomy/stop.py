"""Emergency stop of the autonomy loop: two STOP files, read on every step.

* ``<data>/computer/STOP``  - the owner's global STOP (`bossman stop --all`, palette, Telegram);
* ``<root>/STOP``           - the autonomy STOP (`bossman autonomy stop`, POST /api/autonomy/stop).

Either file stops the loop: the cycle driver checks it on every iteration, the
hand broker refuses every request while it is set (journaled), and the writer /
reviewer sessions poll it while their CLI runs and kill the process tree.
Clearing is separate and explicit: `bossman autonomy resume` clears only the
autonomy STOP; the global STOP is cleared by the owner where it was set.

This module has no dependency on the rest of the package so every layer can use it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

STOP_NAME = "STOP"


def autonomy_stop_path(root: str | os.PathLike) -> Path:
    return Path(root) / STOP_NAME


def global_stop_path(data_dir: str | os.PathLike | None) -> Path | None:
    return Path(data_dir) / "computer" / STOP_NAME if data_dir else None


def _read(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def stop_sources(root: str | os.PathLike, data_dir: str | os.PathLike | None = None) -> dict[str, dict]:
    """{"autonomy": {...}, "computer": {...}} for every STOP file that exists (fail closed: an unreadable
    file still counts as set)."""
    found: dict[str, dict] = {}
    for name, path in (("autonomy", autonomy_stop_path(root)), ("computer", global_stop_path(data_dir))):
        if path is not None and path.exists():
            found[name] = _read(path) or {"by": "unknown"}
    return found


def stop_reason(root: str | os.PathLike, data_dir: str | os.PathLike | None = None) -> str:
    """Empty string when the loop may run, otherwise why it must not."""
    found = stop_sources(root, data_dir)
    if not found:
        return ""
    parts = []
    for name, info in sorted(found.items()):
        why = str(info.get("reason") or "").strip()
        parts.append(f"{name} STOP" + (f" ({why[:120]})" if why else ""))
    return "owner STOP: " + "; ".join(parts)


def request_stop(root: str | os.PathLike, *, by: str = "owner", reason: str = "") -> dict:
    """Persist the autonomy STOP (atomic write). Returns the stored record."""
    path = autonomy_stop_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"by": str(by)[:64], "reason": (reason or "owner STOP")[:300],
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return rec


def clear_stop(root: str | os.PathLike) -> bool:
    """Remove the autonomy STOP only. True when a file was removed."""
    path = autonomy_stop_path(root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def checker(root: str | os.PathLike, data_dir: str | os.PathLike | None = None) -> Callable[[], str]:
    """A zero-argument ``stop_check`` for the cycle, the hand broker and the sessions."""
    return lambda: stop_reason(root, data_dir)


__all__ = ["STOP_NAME", "autonomy_stop_path", "checker", "clear_stop", "global_stop_path", "request_stop",
           "stop_reason", "stop_sources"]
