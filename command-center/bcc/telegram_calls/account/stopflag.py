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
        self.stop_path.write_text(json.dumps({"by": by, "at": time.time()}), encoding="utf-8")

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

    def is_uncertain(self) -> bool:
        s = self._state()
        return bool(s.get("in_flight")) or bool(s.get("uncertain"))

    def acknowledge_uncertain(self) -> None:
        s = self._state()
        s.update({"in_flight": None, "uncertain": False})
        self._write_state(s)

    # ---- history
    def append_history(self, record: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.home / HISTORY_FILE
        lines = path.read_text(encoding="utf-8").splitlines()[-(_HISTORY_KEEP - 1):] if path.is_file() else []
        lines.append(json.dumps(record, ensure_ascii=False))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def history(self, limit: int = 20) -> list[dict]:
        path = self.home / HISTORY_FILE
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))
