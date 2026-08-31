from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

@dataclass(frozen=True)
class StateFragment:
    kind: str
    observed_at: datetime
    payload: Mapping[str, Any]
    source: str
    artifact_ref: str | None = None
    confidence: float = 1.0

@dataclass(frozen=True)
class VisualSnapshot:
    observed_at: datetime
    structured: Mapping[str, Any] = field(default_factory=dict)
    screenshot_refs: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def compact(self) -> Mapping[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "structured": self.structured,
            "screenshot_refs": self.screenshot_refs,
            "conflicts": self.conflicts,
        }
