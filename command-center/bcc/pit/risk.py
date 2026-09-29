from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .vault import PersonaVault, _atomic_json


@dataclass(frozen=True, slots=True)
class RiskState:
    score: int = 0
    events: int = 0
    last_kind: str = ""
    updated_at: str | None = None


class RiskLedger:
    """Local-only defensive telemetry for participant probing.

    The score is not model context, not a personality trait, not an authority
    grant and not exported by PersonaVault.export(). It can only tune the
    non-sensitive discovery cadence and defensive privacy behavior.
    """

    def __init__(self, vault: PersonaVault):
        self.vault = vault

    def _path(self, person_key: str) -> Path:
        return self.vault.ensure(person_key) / "security" / "risk.json"

    def read(self, person_key: str) -> RiskState:
        path = self._path(person_key)
        if not path.is_file():
            return RiskState()
        import json
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return RiskState(
            score=max(0, int(data.get("score", 0))),
            events=max(0, int(data.get("events", 0))),
            last_kind=str(data.get("last_kind", ""))[:80],
            updated_at=data.get("updated_at"),
        )

    def add(self, person_key: str, *, delta: int, kind: str) -> RiskState:
        if delta <= 0:
            return self.read(person_key)
        current = self.read(person_key)
        updated = RiskState(
            score=current.score + int(delta),
            events=current.events + 1,
            last_kind=str(kind)[:80],
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(self._path(person_key), {
            "score": updated.score,
            "events": updated.events,
            "last_kind": updated.last_kind,
            "updated_at": updated.updated_at,
            "schema": "bossman.pit.risk/1",
        })
        return updated
