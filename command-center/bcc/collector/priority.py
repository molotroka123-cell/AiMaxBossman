"""Best-effort low CPU priority, mirroring the pattern already used for child
processes in ``bcc/video_studio/media.py`` (``BELOW_NORMAL_PRIORITY_CLASS``)
and for supervised model processes in ``bcc/studio/providers/sdcpp.py``
(``psutil.Process(...).nice(...)``).

A careful human researcher does not peg the machine while reading a page;
neither should this. Never fatal: a sandboxed/limited-permission process
that cannot renice itself still collects data, just at default priority.
"""
from __future__ import annotations

import os
import sys


def lower_own_priority() -> bool:
    """Best-effort. Returns True if priority was actually lowered."""
    try:
        import psutil
    except ImportError:
        return False
    try:
        proc = psutil.Process(os.getpid())
        if sys.platform == "win32":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            proc.nice(10)
        return True
    except Exception:  # noqa: BLE001 — best effort only, never fatal
        return False
