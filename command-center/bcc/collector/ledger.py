"""Append-only JSONL evidence ledger for one collector run — same shape of
guarantee as ``bcc/market/ledger.py``: every attempt (page fetched, page
skipped, fact found) becomes one line, written and fsynced immediately, so a
STOP mid-run never loses what already happened.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "ledger.jsonl"

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
