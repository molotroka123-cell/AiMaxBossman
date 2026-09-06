from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Iterable

from .models import StateFragment, VisualSnapshot, StateIdentity, _canonical_json, _freeze_json


class StaleVisualStateError(RuntimeError):
    pass


class AmbiguousVisualStateError(RuntimeError):
    pass


def validate_freshness(observed_at: datetime, now: datetime, max_age_seconds: float) -> None:
    for value in (observed_at, now):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("visual observation timestamps must be timezone-aware")
    age = (now - observed_at).total_seconds()
    if age < 0:
        raise StaleVisualStateError("visual observation is in the future")
    if age > max_age_seconds:
        raise StaleVisualStateError("visual state is stale; reobserve before acting")


class VisualStateEngine:
    """Fuse observations; opt in to identity/conflict rejection for action use.

    Legacy callers still receive conflicting fields for display, with conflicts
    recorded. All callers reject stale individual fragments and future times.
    This module executes no action and grants no permission.
    """

    STRUCTURED_KINDS = {"dom", "accessibility", "a11y", "os_accessibility"}

    def __init__(self, max_age_seconds: float = 5.0, *, require_identity: bool = False,
                 min_structured_confidence: float = 0.8):
        if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
            raise ValueError("max_age_seconds must be finite and nonnegative")
        if not math.isfinite(min_structured_confidence) or not 0 <= min_structured_confidence <= 1:
            raise ValueError("min_structured_confidence must be between zero and one")
        self.max_age_seconds = max_age_seconds
        self.require_identity = require_identity
        self.min_structured_confidence = min_structured_confidence

    def fuse(self, fragments: Iterable[StateFragment], *, now: datetime | None = None) -> VisualSnapshot:
        now = now or datetime.now(timezone.utc)
        fragments = list(fragments)
        if not fragments:
            raise ValueError("at least one state fragment is required")
        for fragment in fragments:
            validate_freshness(fragment.observed_at, now, self.max_age_seconds)
            if self.require_identity and (not isinstance(fragment.source, str) or not fragment.source.strip()):
                raise ValueError("actionable observations require an adapter source")
            if not math.isfinite(fragment.confidence) or not 0 <= fragment.confidence <= 1:
                raise ValueError("fragment confidence must be finite and between zero and one")

        identity = fragments[0].identity
        identity_conflict = any(f.identity != identity for f in fragments)
        if self.require_identity and (not isinstance(identity, StateIdentity) or identity_conflict):
            raise AmbiguousVisualStateError("missing or conflicting application/window/document state identity")
        if self.require_identity and not any(f.kind in self.STRUCTURED_KINDS for f in fragments):
            raise AmbiguousVisualStateError("actionable state requires a structured observation")

        structured: dict[str, object] = {}
        screenshot_refs: list[str] = []
        provenance: list[str] = []
        conflicts: list[str] = []
        if identity_conflict:
            conflicts.append("identity: conflicting application/window/document observations")
            identity = None
        seen: dict[str, object] = {}
        for f in sorted(fragments, key=lambda x: (x.kind not in self.STRUCTURED_KINDS, -x.confidence)):
            provenance.append(f"{f.kind}:{f.source}:{f.observed_at.isoformat()}")
            if f.artifact_ref and f.kind in {"screenshot", "vision"}:
                screenshot_refs.append(f.artifact_ref)
            if f.kind in self.STRUCTURED_KINDS:
                if self.require_identity and f.confidence < self.min_structured_confidence:
                    raise AmbiguousVisualStateError("structured observation confidence is insufficient")
                for key, value in f.payload.items():
                    if not isinstance(key, str) or key.startswith("vision_hint."):
                        raise ValueError("structured keys must be strings outside the vision_hint namespace")
                    different = key in seen and (
                        _canonical_json(seen[key]) != _canonical_json(value)
                        if self.require_identity else seen[key] != value
                    )
                    if different:
                        conflicts.append(f"{key}: conflicting structured observations")
                    else:
                        seen[key] = value
                        structured[key] = value
            elif f.kind == "vision":
                for key, value in f.payload.items():
                    structured.setdefault(f"vision_hint.{key}", value)
        if self.require_identity and conflicts:
            raise AmbiguousVisualStateError("; ".join(dict.fromkeys(conflicts)))
        return VisualSnapshot(
            observed_at=max(f.observed_at for f in fragments),
            structured=_freeze_json(structured) if self.require_identity else structured,
            screenshot_refs=tuple(dict.fromkeys(screenshot_refs)),
            provenance=tuple(provenance),
            conflicts=tuple(dict.fromkeys(conflicts)),
            identity=identity,
            oldest_observed_at=min(f.observed_at for f in fragments),
            authoritative_fields=tuple(seen),
        )
