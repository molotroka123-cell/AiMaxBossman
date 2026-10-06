"""Append-only decision journal (JSONL) + de-identified before/after frames. Every record carries the exact build SHA."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2

from .privacy import anonymise


def build_sha() -> str:
    for k in ("BOSSMAN_BUILD_SHA", "GIT_SHA"):
        if os.environ.get(k):
            return os.environ[k]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, cwd=Path(__file__).resolve().parent).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


class Journal:
    def __init__(self, root: Path, session_id: str):
        self.dir = Path(root) / session_id
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "decisions.jsonl"
        self.sha = build_sha()
        self.lock = threading.Lock()
        self.n = 0

    def frame(self, tag: str, bgr, state) -> str:
        with self.lock:
            self.n += 1; name = f"frames/{self.n:04d}_{tag}.png"
        cv2.imwrite(str(self.dir / name), anonymise(bgr, state))
        return name

    def add(self, **rec) -> dict:
        rec = {"t": time.time(), "sha": self.sha, **rec}
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    def read(self) -> list[dict]:
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines()] if self.path.exists() else []
