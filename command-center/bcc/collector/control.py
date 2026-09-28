"""STOP and PAUSE for the collector.

STOP: a file named ``STOP`` inside the run's own data dir (same name and
place as ``bcc/market/collector.py``'s ``<root>/STOP`` and
``bcc/features/tools_computer.py``'s ``STOP_FILE``). Its presence ends the
run immediately and gracefully — whatever was collected so far is written
out; nothing in flight is treated as done.

PAUSE: the single owner-wide file for this rc19 owner-test cycle
(``config.pause_file_path()``). Its presence only pauses — the run polls at
low CPU cost and continues automatically once the file is gone, honouring
STOP even while paused.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config


class Stopped(Exception):
    """Raised to unwind the collection loop cleanly on STOP."""


class RunControl:
    def __init__(self, run_dir: Path, *, pause_file: Path | None = None,
                 sleep=None, poll_s: float = config.PAUSE_POLL_S):
        self.stop_path = Path(run_dir) / config.STOP_FILE_NAME
        self.pause_path = Path(pause_file) if pause_file is not None else config.pause_file_path()
        # Looked up here, not as a parameter default: a parameter default is
        # bound once when this module is first imported, so a test's
        # ``monkeypatch.setattr(control.time, "sleep", ...)`` after that
        # point would silently do nothing. A fresh attribute lookup at
        # construction time picks up the patch.
        self._sleep = sleep if sleep is not None else time.sleep
        self._poll_s = poll_s

    def stopped(self) -> bool:
        return self.stop_path.is_file()

    def paused(self) -> bool:
        return self.pause_path.is_file()

    def check_stop(self) -> None:
        if self.stopped():
            raise Stopped(f"STOP file present: {self.stop_path}")

    def wait_out_pause(self) -> None:
        """Blocks (in small, STOP-checked slices) while PAUSE is present."""
        while self.paused():
            self.check_stop()
            self._sleep(self._poll_s)
        self.check_stop()

    def polite_wait(self, seconds: float) -> None:
        """Sleeps ``seconds`` in small slices, honouring STOP and PAUSE the
        whole time instead of only at the end of the wait."""
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            self.check_stop()
            self.wait_out_pause()
            step = min(self._poll_s, remaining)
            self._sleep(step)
            remaining -= step
        self.check_stop()
