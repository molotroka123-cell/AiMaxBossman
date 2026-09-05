"""Failed-approaches memory (V3.1) — реализация FailureMemoryPort из contracts.py.

Смысл ровно один: после рестарта или смены модели новая модель не должна
заново наступать на подход, который уже провалился. Поэтому запись переживает
процесс и достаётся по сигнатуре шага вместе с контекстом.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

FILENAME = "failure_memory.json"


class FailureMemory:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / FILENAME

    def _all(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return list(json.loads(self.path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            return []                      # битый журнал не должен ронять прогон

    def record(self, event: Mapping[str, Any]) -> None:
        rows = self._all()
        rows.append({**dict(event), "at": datetime.now(timezone.utc).isoformat()})
        self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def query(self, signature: str, limit: int = 20) -> Sequence[Mapping[str, Any]]:
        rows = [r for r in self._all() if str(r.get("signature", "")) == signature]
        return rows[-limit:]

    def all_signatures(self) -> set[str]:
        return {str(r.get("signature", "")) for r in self._all()}
