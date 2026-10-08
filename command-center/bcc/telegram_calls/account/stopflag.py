"""Call-module STOP marker + last-outcome/uncertainty state + call history (secret- and transcript-free)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..settings import calls_home
from ..types import Outcome, UNCERTAIN_OUTCOMES

STOP_FILE = "STOP"
STATE_FILE = "state.json"
HISTORY_FILE = "history.jsonl"
_HISTORY_KEEP = 200


class CallState:
    """Durable little facts that must survive a restart: STOP, and 'the last call ended in an unknown state'."""

    def __init__(self, home: Path | None = None):
        self.home = home or calls_home()

    # ---- STOP (set first by every STOP path; cleared only by the owner)
    @property
    def stop_path(self) -> Path:
        return self.home / STOP_FILE

    def stop_is_set(self) -> bool:
        return self.stop_path.is_file()

    def set_stop(self, by: str = "owner") -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps({"by": by, "at": time.time()})
        try:
            self.stop_path.write_text(payload, encoding="utf-8")
        except PermissionError:
            # a STOP file left with an empty ACL by an older build: it cannot be written, but the owner may delete and recreate it
            self.stop_path.unlink(missing_ok=True)
            self.stop_path.write_text(payload, encoding="utf-8")

    def clear_stop(self) -> None:
        try:
            self.stop_path.unlink()
        except FileNotFoundError:
            pass

    # ---- uncertainty after the previous call
    def _state(self) -> dict:
        try:
            return json.loads((self.home / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_state(self, data: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.home / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.home / STATE_FILE)

    def note_call_started(self, call_id: str) -> None:
        """Written BEFORE the dial: if the process dies mid-call this stays and the next dial needs a confirm."""
        self._write_state({"in_flight": call_id, "started": time.time(), "last_outcome": self._state().get("last_outcome")})

    def note_call_finished(self, call_id: str, outcome: Outcome | None) -> None:
        uncertain = outcome is None or outcome in UNCERTAIN_OUTCOMES
        self._write_state({"in_flight": None, "last_call_id": call_id, "last_outcome": outcome.value if outcome else "unknown",
                           "uncertain": uncertain, "finished": time.time()})

    def is_uncertain(self, *, call_in_progress: bool = False) -> bool:
        """True when the PREVIOUS call ended unknown (or died in flight). While a call is in progress its own `in_flight`
        marker is not an uncertainty about the previous one (the dial guard never passes that flag: it stays strict)."""
        s = self._state()
        return (bool(s.get("in_flight")) and not call_in_progress) or bool(s.get("uncertain"))

    def acknowledge_uncertain(self) -> None:
        s = self._state()
        s.update({"in_flight": None, "uncertain": False})
        self._write_state(s)

    # ---- history
    # History is display/post-call data, never a dial gate. A history file with a non-UTF-8 byte run (torn sector, a
    # foreign editor's encoding) must not raise UnicodeDecodeError out of the end-of-call path: the worker only guards
    # these writes against OSError, so the old strict read left it «in a call» and swallowed the record.
    def _history_lines(self, path: Path) -> list[str]:
        return path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []

    def append_history(self, record: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.home / HISTORY_FILE
        lines = self._history_lines(path)[-(_HISTORY_KEEP - 1):]
        lines.append(json.dumps(record, ensure_ascii=False))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def history(self, limit: int = 20) -> list[dict]:
        if limit <= 0:                                            # [-0:] would be the WHOLE file
            return []
        out = []
        for line in self._history_lines(self.home / HISTORY_FILE)[-limit:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):                             # callers do row.get(...): a bare number/list line is garbage
                out.append(row)
        return list(reversed(out))
