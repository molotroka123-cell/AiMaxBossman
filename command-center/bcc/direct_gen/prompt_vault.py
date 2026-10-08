"""Owner-only encrypted history of generation prompts (hidden vault).

Lives in <data_dir>/vault-prompts/ with its own Fernet key (owner-only ACL), apart from job.json.
Only the owner's own jobs are recorded; no listing endpoint other than the owner one reads it.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..secrets import Vault

_LOCK = threading.Lock()


class PromptVault:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "vault-prompts"
        self.file = self.dir / "prompts.enc"
        self._vault = Vault(self.dir)

    def _rows(self) -> list[dict[str, Any]]:
        if not self.file.exists():
            return []
        rows = []
        for line in self.file.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(self._vault.decrypt(line)))
            except Exception:  # noqa: BLE001 - one damaged line must not hide the rest
                continue
        return rows

    def record(self, job: dict[str, Any]) -> None:
        if job.get("participant") != "owner":
            return
        row = {"job_id": job["job_id"], "at": job.get("created_at"), "model": job.get("model"),
               "kind": job.get("kind"), "prompt": job.get("raw_prompt"), "negative": job.get("negative"),
               "effective_prompt": job.get("effective_prompt"), "retry_of": job.get("retry_of")}
        with _LOCK:
            if any(r.get("job_id") == row["job_id"] for r in self._rows()):
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.file.open("a", encoding="utf-8") as fh:
                fh.write(self._vault.encrypt(json.dumps(row, ensure_ascii=False)) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    def search(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        rows = [r for r in self._rows() if not q or q in json.dumps(r, ensure_ascii=False).lower()]
        return rows[::-1][: max(1, min(int(limit), 1000))]
