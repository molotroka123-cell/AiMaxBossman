"""Shared guards for long local-model jobs on the owner machine.

* ``wait_if_paused`` blocks before every model call while the owner-test PAUSE
  file exists (polls every 60 s).
* ``lower_priority`` drops the current process to BELOW_NORMAL on Windows
  (``nice`` elsewhere) so owner work keeps priority.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable

PAUSE_FILE = Path(os.getenv("BOSSMAN_RC19_PAUSE_FILE", r"C:\Users\asd\Bossman\rc19-owner-test.PAUSE"))


def wait_if_paused(*, pause_file: Path | None = None, poll_s: float = 60.0,
                   sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] | None = None) -> int:
    """Return the number of polls spent waiting (0 when not paused)."""
    path = pause_file or PAUSE_FILE
    polls = 0
    while path.exists():
        if log and polls == 0:
            log(f"PAUSE file present ({path}); waiting")
        polls += 1
        sleep(poll_s)
    return polls


def lower_priority() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            BELOW_NORMAL = 0x00004000
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            k32.SetPriorityClass.restype = wintypes.BOOL
            ok = k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
            return "BELOW_NORMAL" if ok else "unchanged"
        except Exception:  # noqa: BLE001
            return "unchanged"
    try:
        os.nice(10)
        return "nice+10"
    except OSError:
        return "unchanged"
