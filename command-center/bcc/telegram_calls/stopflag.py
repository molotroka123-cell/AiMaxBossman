"""Persistent call-STOP flag (file in the Command Center data dir; survives restarts).

Blocks dialing until the owner explicitly resumes. ``is_set`` is also true while the EXISTING
global computer STOP file (``<data_dir>/computer/STOP``, see features/tools_computer.py) is present:
"dialing is blocked while the global STOP is set". ``clear`` only ever removes OUR file - resuming
calls never resumes the computer, and the call flag never hides a global STOP.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .settings import calls_dir

FILE_NAME = "STOP"
GLOBAL_STOP_REL = ("computer", "STOP")   # bcc.features.tools_computer.STOP_FILE


class StopFlag:
    def __init__(self, data_dir: Path | str):
        self.path = calls_dir(data_dir) / FILE_NAME
        self.global_path = Path(data_dir).joinpath(*GLOBAL_STOP_REL)

    def set(self, by: str = "owner") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{FILE_NAME}.{os.getpid()}.tmp")
        tmp.write_text(f"{str(by)[:40]}\n{time.time()}\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def call_stop_set(self) -> bool:
        return self.path.is_file()

    def global_stop_set(self) -> bool:
        return self.global_path.is_file()

    def is_set(self) -> bool:
        return self.call_stop_set() or self.global_stop_set()

    def info(self) -> dict:
        return {"call_stop": self.call_stop_set(), "global_stop": self.global_stop_set()}
