from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterable
from .models import StateFragment, VisualSnapshot

class StaleVisualStateError(RuntimeError):
    pass

class VisualStateEngine:
    """Fuses DOM/a11y/vision references without turning model guesses into authority."""
    STRUCTURED_KINDS = {"dom", "accessibility", "a11y", "os_accessibility"}

    def __init__(self, max_age_seconds: float = 5.0):
        self.max_age_seconds = max_age_seconds

    def fuse(self, fragments: Iterable[StateFragment], *, now: datetime | None = None) -> VisualSnapshot:
        now = now or datetime.now(timezone.utc)
        fragments = list(fragments)
        if not fragments:
            raise ValueError("at least one state fragment is required")
        freshest = max(f.observed_at for f in fragments)
        if (now - freshest).total_seconds() > self.max_age_seconds:
            raise StaleVisualStateError("visual state is stale")

        structured: dict[str, object] = {}
        screenshot_refs: list[str] = []
        provenance: list[str] = []
        conflicts: list[str] = []
        seen: dict[str, object] = {}
        for f in sorted(fragments, key=lambda x: (x.kind not in self.STRUCTURED_KINDS, -x.confidence)):
            provenance.append(f"{f.kind}:{f.source}:{f.observed_at.isoformat()}")
            if f.artifact_ref and f.kind in {"screenshot", "vision"}:
                screenshot_refs.append(f.artifact_ref)
            if f.kind in self.STRUCTURED_KINDS:
                for key, value in f.payload.items():
                    if key in seen and seen[key] != value:
                        conflicts.append(f"{key}: conflicting structured observations")
                    else:
                        seen[key] = value
                        structured[key] = value
            elif f.kind == "vision":
                # Vision is evidence, not authority. Only fill fields absent from structured state.
                for key, value in f.payload.items():
                    structured.setdefault(f"vision_hint.{key}", value)
        return VisualSnapshot(
            observed_at=freshest,
            structured=structured,
            screenshot_refs=tuple(dict.fromkeys(screenshot_refs)),
            provenance=tuple(provenance),
            conflicts=tuple(dict.fromkeys(conflicts)),
        )
