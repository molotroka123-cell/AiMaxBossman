from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from types import MappingProxyType
from typing import Any, Mapping


def _canonical_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("semantic state and action keys must be strings")
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _freeze_json(value: Any) -> Any:
    # Roundtrip detaches caller-owned containers and rejects non-JSON values.
    def freeze(item: Any) -> Any:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item
    return freeze(json.loads(_canonical_json(value)))


@dataclass(frozen=True)
class StateIdentity:
    """Adapter-supplied identity; revisions change on focus/modal/state changes.

    Document IDs must not be recycled across navigation. This is an observation
    binding, not evidence of authorization or a trusted completion receipt.
    """

    application_id: str
    window_id: str
    document_id: str
    navigation_generation: int
    state_revision: int

    def __post_init__(self) -> None:
        for name in ("application_id", "window_id", "document_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")
        for name in ("navigation_generation", "state_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True)
class StateFragment:
    kind: str
    observed_at: datetime
    payload: Mapping[str, Any]
    source: str
    artifact_ref: str | None = None
    confidence: float = 1.0
    identity: StateIdentity | None = None


@dataclass(frozen=True)
class VisualSnapshot:
    observed_at: datetime
    structured: Mapping[str, Any] = field(default_factory=dict)
    screenshot_refs: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    identity: StateIdentity | None = None
    oldest_observed_at: datetime | None = None
    authoritative_fields: tuple[str, ...] = ()

    def compact(self) -> Mapping[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "structured": (json.loads(_canonical_json(self.structured))
                           if isinstance(self.structured, MappingProxyType) else self.structured),
            "screenshot_refs": self.screenshot_refs,
            "conflicts": self.conflicts,
        }
