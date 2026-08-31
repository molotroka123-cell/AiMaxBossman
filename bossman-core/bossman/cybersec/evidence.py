"""Evidence ledger — улики эпизода, пригодные для последующего разбора.

Отличие от эталонной реализации из ZIP: там улики редактировались регуляркой
ПО JSON-ТЕКСТУ и затем парсились обратно (`json.loads(redact(json.dumps(...)))`).
Секрет со скобкой/кавычкой ломал JSON, а падение записи улик = потеря улик.
Здесь редактируем ПОЗНАЧЕННО каноническим `obs.redact_obj`, JSON собирается
уже из очищенного объекта.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from ..obs import redact_obj


class EvidenceLedger:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_episode(self, scenario_id: str, records: dict[str, Any]) -> Path:
        safe = redact_obj(records)          # по значениям, не по тексту JSON
        d = self.root / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "episode.json").write_text(
            json.dumps(safe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return d
