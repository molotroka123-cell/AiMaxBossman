"""Drop-folder watcher. Polling (no extra dependency, negligible CPU between scans).

* A file is taken only after it stopped changing for ``settle`` seconds, so a
  copy still in progress is never half-ingested.
* Originals stay in the inbox (copy-only). ``watch-state.json`` in the library
  root remembers (size, mtime) per path, so an unchanged file is not re-hashed
  on every scan; a changed file is taken again (and deduplicated by sha256).
* One memory index call per batch; entries left ``indexed: false`` (backend
  down) are retried every ``retry_every`` seconds.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from .fsutil import atomic_write_text
from .library import ADDED, ERROR, MEM_PENDING, MEM_WRITTEN, Library, Outcome, now_iso

STATE = "watch-state.json"
MAX_ATTEMPTS = 3
SKIP_PREFIX = ("~$", ".~lock", ".")
SKIP_SUFFIX = (".tmp", ".part", ".crdownload", ".download", ".partial", ".!ut", ".opdownload")
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}


def _skip(name: str) -> bool:
    low = name.lower()
    return low in SKIP_NAMES or low.startswith(SKIP_PREFIX) or low.endswith(SKIP_SUFFIX)


def candidates(inbox: Path) -> list[Path]:
    out = []
    for path in sorted(Path(inbox).rglob("*")):
        if any(_skip(part) for part in path.relative_to(inbox).parts):
            continue
        if path.is_file():
            out.append(path)
    return out


def _load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def scan_once(lib: Library, inbox: Path, *, settle: float = 5.0,
              notify: Callable[[Outcome], None] | None = None, clock=time.time) -> list[Outcome]:
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    state_path = lib.root / STATE
    state = _load_state(state_path)
    files = state.setdefault("files", {})
    outcomes: list[Outcome] = []
    for path in candidates(inbox):
        try:
            st = path.stat()
        except OSError:
            continue
        key = os.path.normcase(str(path.resolve()))
        sig = [st.st_size, st.st_mtime_ns]
        prev = files.get(key) or {}
        same = prev.get("sig") == sig
        if same and (prev.get("status") != ERROR or int(prev.get("attempts", 0)) >= MAX_ATTEMPTS):
            continue
        if clock() - st.st_mtime < settle:
            continue                                   # still being written: next scan
        outcome = lib.intake(path, index=False)
        files[key] = {"sig": sig, "status": outcome.status, "id": outcome.id, "reason": outcome.reason[:200],
                      "at": now_iso(), "attempts": int(prev.get("attempts", 0)) + 1 if same else 1}
        atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=1))
        outcomes.append(outcome)
        if notify is not None:
            try:
                notify(outcome)
            except Exception:  # noqa: BLE001 - a failed notification never blocks the library
                pass
    if any(o.status == ADDED and (o.entry or {}).get("memory", {}).get("state") == MEM_WRITTEN for o in outcomes):
        lib.index_now()
    return outcomes


def retry_pending(lib: Library, *, every: float = 300.0, clock=time.time) -> dict | None:
    if lib.memory is None or not lib.memory.enabled:
        return None
    state_path = lib.root / STATE
    state = _load_state(state_path)
    if clock() - float(state.get("last_retry", 0)) < every:
        return None
    pending = [e["id"] for e in lib.load()["entries"].values()
               if (e.get("memory") or {}).get("state") in (MEM_PENDING, MEM_WRITTEN)]
    state["last_retry"] = clock()
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=1))
    if not pending:
        return None
    return lib.reindex(pending)


def watch(lib: Library, inbox: Path, *, interval: float = 15.0, settle: float = 5.0, once: bool = False,
          notify: Callable[[Outcome], None] | None = None, log: Callable[[str], None] = print,
          stop: threading.Event | None = None, retry_every: float = 300.0) -> int:
    stop = stop or threading.Event()
    log(f"doc_intake watch: папка {inbox} → библиотека {lib.root} (каждые {interval:.0f} с)")
    while True:
        for outcome in scan_once(lib, inbox, settle=settle, notify=notify):
            log(outcome.line())
        retried = retry_pending(lib, every=retry_every)
        if retried:
            log(f"память: переиндексация {retried}")
        if once or stop.wait(interval):
            return 0
