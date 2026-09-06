"""Deterministic, model-free observers producing evaluate-shaped observations.

This is the sensing edge of the V5 Steward. Everything here is a pure function
of an owner-named local path (or an injected local check) and an explicit clock
reading. An observer reports *facts* — existence, byte digest, size, entry count,
tree digest — never an interpretation. `MODEL_TEXT != PROOF` is enforced here by
construction: this module never imports a model client, never opens a socket,
never captures a screen and never reads an ambient clock.

Invariants carried by this module rather than by caller discipline:

* No implicit enrollment. An observer can only be constructed against an
  `EnrolledSource`, so nothing is ever scanned that an owner did not name.
* `observation_id` is a content digest over source ref, source revision,
  objective digest, observation time and values. The same fact observed twice at
  the same instant collapses to one identity, and a replayed or reordered batch
  is detectable by identity rather than by trust.
* `bossman_shared.objective_spec.evaluate` requires an observation dict with
  *exactly* eight keys, so provenance never travels inside the evaluated record.
  It lives on the observation object and binds into evidence through
  `provenance_digest`.
* Collection is bounded: only enrolled sources, never a duplicated source ref,
  never more observations than the objective's remaining cumulative budget.
* `should_observe` decides "did the world actually change" from digests alone,
  so an idle Steward makes exactly zero model calls.

Deliberate non-goals. No scheduler, no persistence, no admission authority, no
evidence signer, no network or remote fleet observer. An observation is input to
a projection; it is never itself a grant, and it is never proof of an effect.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .mission_ir import MissionIRValidationError, _canonical, _json_types
from .objective_store import ObjectiveRuntimeState

OBSERVER_VERSION = "v5-observer-1"

# Bounds are deliberately small: an observer is a sensor, not a crawler. A flood
# of files must cost a bounded amount of work, not an unbounded scan.
DEFAULT_MAX_ENTRIES = 512
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024


class ObservationError(ValueError):
    """The observer refused to emit; a refusal is never a fact about the world."""


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip() or "\x00" in value:
        raise ObservationError(f"{name}: nonempty canonical string required")
    return value


def _clock(now: Any) -> float:
    if type(now) not in (int, float) or type(now) is bool or now != now or now < 0:
        raise ObservationError("observed_at must be an explicit nonnegative clock reading")
    return float(now)


def _digest(payload: Mapping[str, Any]) -> str:
    try:
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    except MissionIRValidationError as exc:
        raise ObservationError(str(exc)) from exc


def _json_mapping(values: Any, name: str) -> dict[str, Any]:
    """Accept only a JSON object of JSON values, normalised through canonical form."""
    if type(values) is not dict:
        raise ObservationError(f"{name}: JSON object required")
    try:
        _json_types(values)
        return json.loads(_canonical(values))
    except MissionIRValidationError as exc:
        raise ObservationError(f"{name}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class EnrolledSource:
    """One owner-named source binding. There is no ambient enrollment."""

    source_ref: str
    source_revision: str
    owner_id: str
    scope_id: str
    objective_digest: str

    def __post_init__(self) -> None:
        for name in ("source_ref", "source_revision", "owner_id", "scope_id", "objective_digest"):
            _text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Observation:
    """One deterministic fact reading plus the provenance that produced it."""

    observation_id: str
    owner_id: str
    scope_id: str
    objective_digest: str
    source_ref: str
    source_revision: str
    observed_at: float
    values: dict[str, Any]
    provenance: dict[str, Any]

    def record(self) -> dict[str, Any]:
        """The exactly-eight-key dict `objective_spec.evaluate` accepts verbatim.

        Provenance is deliberately absent: adding a ninth key would make every
        predicate resolve `malformed_observation`.
        """
        return {
            "observation_id": self.observation_id,
            "owner_id": self.owner_id,
            "scope_id": self.scope_id,
            "objective_digest": self.objective_digest,
            "source_ref": self.source_ref,
            "source_revision": self.source_revision,
            "observed_at": self.observed_at,
            "values": _json_mapping(self.values, "values"),
        }

    @property
    def provenance_digest(self) -> str:
        """Bind provenance into evidence without polluting the evaluated record."""
        return _digest(self.provenance)

    @property
    def value_digest(self) -> str:
        """Digest of the *relevant* world state: revision and values, not time.

        Observation time is excluded on purpose. Re-reading an unchanged file a
        thousand times must yield one value digest, or the no-change gate would
        report change on every tick and defeat itself.
        """
        return _digest({
            "source_ref": self.source_ref,
            "source_revision": self.source_revision,
            "values": self.values,
        })


def observation_identity(*, source_ref: str, source_revision: str, objective_digest: str,
                         observed_at: float, values: Mapping[str, Any]) -> str:
    """Content identity of one observation; recomputable by any verifier."""
    return _digest({
        "source_ref": _text(source_ref, "source_ref"),
        "source_revision": _text(source_revision, "source_revision"),
        "objective_digest": _text(objective_digest, "objective_digest"),
        "observed_at": _clock(observed_at),
        "values": _json_mapping(values, "values"),
    })


def _observation(enrolled: EnrolledSource, values: Mapping[str, Any], *, now: float,
                 provenance: Mapping[str, Any]) -> Observation:
    clean = _json_mapping(values, "values")
    stamp = _clock(now)
    return Observation(
        observation_id=observation_identity(
            source_ref=enrolled.source_ref, source_revision=enrolled.source_revision,
            objective_digest=enrolled.objective_digest, observed_at=stamp, values=clean),
        owner_id=enrolled.owner_id,
        scope_id=enrolled.scope_id,
        objective_digest=enrolled.objective_digest,
        source_ref=enrolled.source_ref,
        source_revision=enrolled.source_revision,
        observed_at=stamp,
        values=clean,
        provenance=_json_mapping(dict(provenance), "provenance"),
    )


class _Observer:
    """Base binding: an observer exists only for one enrolled source."""

    kind = "observer"

    def __init__(self, enrolled: EnrolledSource) -> None:
        if type(enrolled) is not EnrolledSource:
            raise ObservationError("an observer requires an explicit EnrolledSource")
        self.enrolled = enrolled

    @property
    def source_ref(self) -> str:
        return self.enrolled.source_ref

    def observe(self, *, now: float) -> Observation:  # pragma: no cover - interface
        raise NotImplementedError

    def _provenance(self, target: str) -> dict[str, Any]:
        return {"kind": self.kind, "target": target, "observer_version": OBSERVER_VERSION}


def _hash_file(path: Path, *, max_bytes: int) -> tuple[str | None, int]:
    """Chunked digest so a large named file cannot be read into memory whole."""
    size = path.stat().st_size
    if size > max_bytes:
        return None, size
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest(), size


class FileStateObserver(_Observer):
    """Facts about one owner-named file path. Never opinions about its contents."""

    kind = "local_file"

    def __init__(self, enrolled: EnrolledSource, path: str | os.PathLike[str], *,
                 max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        super().__init__(enrolled)
        self.path = Path(path)
        if type(max_file_bytes) is not int or max_file_bytes <= 0:
            raise ObservationError("max_file_bytes must be a positive integer")
        self.max_file_bytes = max_file_bytes

    def observe(self, *, now: float) -> Observation:
        # A symlink is not the file the owner named: it can be repointed between
        # two observations, so link identity is never treated as file identity.
        if self.path.is_symlink() or not self.path.is_file():
            values: dict[str, Any] = {"exists": False, "sha256": None, "size_bytes": 0}
        else:
            digest, size = _hash_file(self.path, max_bytes=self.max_file_bytes)
            values = {"exists": True, "sha256": digest, "size_bytes": size}
        return _observation(self.enrolled, values, now=now,
                            provenance=self._provenance(str(self.path)))


class DirectoryStateObserver(_Observer):
    """Bounded, sorted, symlink-refusing facts about one owner-named directory."""

    kind = "local_directory"

    def __init__(self, enrolled: EnrolledSource, root: str | os.PathLike[str], *,
                 max_entries: int = DEFAULT_MAX_ENTRIES, max_depth: int = DEFAULT_MAX_DEPTH,
                 max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        super().__init__(enrolled)
        self.root = Path(root)
        for name, value in (("max_entries", max_entries), ("max_depth", max_depth),
                            ("max_file_bytes", max_file_bytes)):
            if type(value) is not int or value <= 0:
                raise ObservationError(f"{name} must be a positive integer")
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.max_file_bytes = max_file_bytes

    def observe(self, *, now: float) -> Observation:
        if self.root.is_symlink() or not self.root.is_dir():
            values: dict[str, Any] = {
                "exists": False, "entry_count": 0, "total_size_bytes": 0,
                "tree_digest": None, "truncated": False,
                "symlinks_skipped": 0, "escaped_symlinks": 0,
            }
            return _observation(self.enrolled, values, now=now,
                                provenance=self._provenance(str(self.root)))
        rows, flags = self._scan()
        values = {
            "exists": True,
            "entry_count": len(rows),
            "total_size_bytes": sum(row[1] for row in rows),
            "tree_digest": _digest({"entries": [list(row) for row in rows]}),
            "truncated": flags["truncated"],
            "symlinks_skipped": flags["symlinks_skipped"],
            "escaped_symlinks": flags["escaped_symlinks"],
        }
        return _observation(self.enrolled, values, now=now,
                            provenance=self._provenance(str(self.root)))

    def _scan(self) -> tuple[list[tuple[str, int, str | None]], dict[str, Any]]:
        real_root = Path(os.path.realpath(self.root))
        rows: list[tuple[str, int, str | None]] = []
        flags = {"truncated": False, "symlinks_skipped": 0, "escaped_symlinks": 0}

        def walk(directory: Path, depth: int) -> None:
            if flags["truncated"]:
                return
            if depth > self.max_depth:
                flags["truncated"] = True
                return
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
            for entry in entries:
                if len(rows) >= self.max_entries:
                    flags["truncated"] = True
                    return
                child = Path(entry.path)
                if entry.is_symlink():
                    # Never traversed. A link that resolves outside the named root
                    # is counted separately: that is an attempted scope escape.
                    flags["symlinks_skipped"] += 1
                    if not Path(os.path.realpath(child)).is_relative_to(real_root):
                        flags["escaped_symlinks"] += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(child, depth + 1)
                    if flags["truncated"]:
                        return
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                digest, size = _hash_file(child, max_bytes=self.max_file_bytes)
                rows.append((child.relative_to(self.root).as_posix(), size, digest))

        walk(self.root, 1)
        rows.sort()
        return rows, flags


class ScheduledCheckObserver(_Observer):
    """A bounded local check supplied by the caller, validated as plain JSON.

    The injected callable is the only extension point, and its output is treated
    as untrusted: anything that is not a JSON object of JSON values is refused
    rather than coerced. A refusal degrades the predicate to UNKNOWN, which is
    the correct reading of "we do not know".
    """

    kind = "scheduled_check"

    def __init__(self, enrolled: EnrolledSource, check: Callable[[], Mapping[str, Any]], *,
                 target: str = "local-check") -> None:
        super().__init__(enrolled)
        if not callable(check):
            raise ObservationError("scheduled check requires a callable returning JSON values")
        self.check = check
        self.target = _text(target, "target")

    def observe(self, *, now: float) -> Observation:
        produced = self.check()
        values = _json_mapping(produced, "check values")
        return _observation(self.enrolled, values, now=now,
                            provenance=self._provenance(self.target))


@dataclass(frozen=True, slots=True)
class ObserveVerdict:
    """Deterministic answer to "did relevant world state change"; never a model call."""

    source_ref: str
    changed: bool
    reason: str
    value_digest: str


def should_observe(observation: Observation, *, last_value_digest: str | None) -> ObserveVerdict:
    """Gate the model on real change: no relevant change means no model call.

    Comparison is over the value digest, which excludes observation time, so a
    steady world produces `changed=False` forever and the idle model-call count
    is exactly zero.
    """
    if type(observation) is not Observation:
        raise ObservationError("a produced Observation is required")
    current = observation.value_digest
    if last_value_digest is None:
        return ObserveVerdict(observation.source_ref, True, "first_observation", current)
    if type(last_value_digest) is not str:
        raise ObservationError("last_value_digest must be a digest string or None")
    if last_value_digest == current:
        return ObserveVerdict(observation.source_ref, False, "no_change", current)
    return ObserveVerdict(observation.source_ref, True, "world_state_changed", current)


def should_observe_batch(batch: ObservationBatch,
                         last_digests: Mapping[str, str]) -> tuple[ObserveVerdict, ...]:
    """Per-source verdicts for a whole batch, in deterministic source order."""
    if type(batch) is not ObservationBatch:
        raise ObservationError("an ObservationBatch is required")
    if not isinstance(last_digests, Mapping):
        raise ObservationError("last_digests must be a mapping of source_ref to digest")
    return tuple(should_observe(o, last_value_digest=last_digests.get(o.source_ref))
                 for o in batch.observations)


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    """One bounded collection round: what was observed and what was refused."""

    observations: tuple[Observation, ...]
    dropped: tuple[tuple[str, str], ...]

    def records(self) -> list[dict[str, Any]]:
        """Evaluate-shaped records, ready for `objective_spec.evaluate` verbatim."""
        return [o.record() for o in self.observations]

    def provenance(self) -> dict[str, dict[str, Any]]:
        """Provenance kept beside the records, keyed by observation identity."""
        return {o.observation_id: dict(o.provenance) for o in self.observations}

    def value_digests(self) -> dict[str, str]:
        return {o.source_ref: o.value_digest for o in self.observations}


def collect(observers: Sequence[_Observer], *, state: ObjectiveRuntimeState,
            max_observations: int, now: float) -> ObservationBatch:
    """Observe only the enrolled sources of one objective, within its budget.

    Refusals are explicit and returned, never silent: an unenrolled source, a
    source claimed twice in one round, or a source beyond the objective's
    remaining cumulative observation budget is dropped with a reason. A duplicate
    source ref drops *every* claimant, because picking a winner would be
    last-write-wins over a contested fact.
    """
    if type(state) is not ObjectiveRuntimeState:
        raise ObservationError("collection requires the durable ObjectiveRuntimeState")
    if type(max_observations) is not int or max_observations < 0:
        raise ObservationError("max_observations must be a nonnegative integer")
    if type(observers) is str or not isinstance(observers, Sequence):
        raise ObservationError("observers must be a sequence")
    _clock(now)

    seen: dict[str, int] = {}
    for observer in observers:
        if not isinstance(observer, _Observer):
            raise ObservationError("only enrolled observers may be collected")
        seen[observer.source_ref] = seen.get(observer.source_ref, 0) + 1

    enrolled = set(state.enrolled_sources)
    kept: list[_Observer] = []
    dropped: list[tuple[str, str]] = []
    for observer in observers:
        ref = observer.source_ref
        binding = observer.enrolled
        if seen[ref] > 1:
            dropped.append((ref, "duplicate_source"))
        elif ref not in enrolled:
            dropped.append((ref, "not_enrolled"))
        elif (binding.owner_id != state.owner_id or binding.scope_id != state.scope_id
                or binding.objective_digest != state.spec_digest):
            dropped.append((ref, "identity_mismatch"))
        else:
            kept.append(observer)

    remaining = max_observations - state.observations_used
    kept.sort(key=lambda o: o.source_ref)
    if remaining <= 0:
        dropped.extend((o.source_ref, "quota_exhausted") for o in kept)
        kept = []
    elif len(kept) > remaining:
        dropped.extend((o.source_ref, "batch_quota") for o in kept[remaining:])
        kept = kept[:remaining]

    observations = tuple(observer.observe(now=now) for observer in kept)
    return ObservationBatch(observations, tuple(sorted(dropped)))
