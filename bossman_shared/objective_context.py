"""Bounded, deterministic context assembly for V5 execution calls (anti-dumbness).

V5's failure mode is context pollution: every objective, every observation, all
memory, the whole Fleet, every skill and every tool poured into one model call
measurably degrades the model. This module makes "never dump everything" an
enforced property rather than advice.

Invariants
  * A slice carries ONLY: current mission, the relevant predicates of THIS
    objective, the LATEST observation per relevant source, applicable policy,
    the selected skill (if its confidence clears the threshold), the selected
    capabilities/tools and the current verified world state.
  * Refusal, not truncation, for out-of-scope input: other objectives, full
    history, raw logs, the whole tool registry and unscoped memory raise.
  * MEMORY_DATA != POLICY_AUTHORITY. Memory enters only as labelled data with
    provenance; it can never be read as policy and never widens capabilities.
  * `select_capabilities` can only NARROW the authorized set. A tool whose
    required grant is not currently held is excluded, with a reason.
  * Budget pressure drops WHOLE trailing sections in a documented priority
    order; the policy/invariant head is never sliced (mirrors the deterministic
    prefix/volatile-tail split already used by tools/context_slice.py, which
    slices files for a repo; this module slices objective runtime state and
    reuses only its ordering discipline, not its code).
  * `context_digest` reproduces a slice exactly and is stable under reordered
    inputs, so a slice can be bound into evidence.

Non-goals (deliberate)
  * No model call, no summarization, no embedding, no ranking heuristics. Older
    observations are DROPPED, never model-compressed — a summary is not evidence.
  * No authorization. A slice never grants anything; grants are inputs.
  * No storage/IO. Pure function over caller-supplied, already-trusted values.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bossman_shared.mission_ir import MissionIR
from bossman_shared.objective_spec import ObjectiveSpec

SCHEMA_VERSION = 1

# Bounds are part of the contract: a caller may tighten them, never loosen them.
MAX_OBSERVATIONS = 16
MAX_TOOLS = 12
MAX_MEMORY_RECORDS = 8
DEFAULT_BYTE_BUDGET = 32_768
SKILL_CONFIDENCE_THRESHOLD = 0.70

# Priority order. The first _HEAD_SECTIONS entries are the policy/invariant head
# and are never dropped; the tail is dropped whole, last section first.
SECTION_ORDER = (
    "policy",
    "mission",
    "objective_predicates",
    "world_state",
    "observations",
    "capabilities",
    "skill",
    "memory",
)
HEAD_SECTIONS = SECTION_ORDER[:3]

# Names a caller might reach for when it wants to "just add a bit more". Each one
# is a known intelligence regression, so each one is refused by name.
FORBIDDEN_EXTRA_KEYS = frozenset({
    "objectives", "other_objectives", "all_objectives", "objective_index",
    "history", "full_history", "timeline", "transcript",
    "logs", "raw_logs", "journal",
    "tools", "tool_registry", "all_tools", "capability_registry",
    "memory", "memory_dump", "unscoped_memory", "fleet", "world",
})

MEMORY_LABEL = "MEMORY_DATA"
MEMORY_AUTHORITY = "none"
UNKNOWN = "UNKNOWN"


class ContextRefusal(ValueError):
    """Raised instead of silently including out-of-scope or oversized context."""


def _refuse(message: str) -> None:
    raise ContextRefusal(message)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:  # pragma: no cover - defensive
        raise ContextRefusal("context must contain finite JSON values") from exc


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value:
        _refuse(f"{name}: non-empty text required")
    return value


def _finite(value: Any, name: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or value != value or value in (float("inf"), float("-inf")):
        _refuse(f"{name}: finite number required")
    return float(value)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Recalled text. Data with provenance — never policy, never authority."""

    memory_id: str
    objective_digest: str
    observed_at: float
    text: str
    provenance: str


@dataclass(frozen=True, slots=True)
class WorldFact:
    """Verified fact storage, not model belief. Stale facts render as UNKNOWN."""

    fact_id: str
    source_ref: str
    scope_ref: str
    observed_at: float
    max_age_seconds: float
    provenance: str
    value: Any


@dataclass(frozen=True, slots=True)
class SkillCandidate:
    skill_id: str
    confidence: float
    digest: str


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """One registry row: a tool, the capability it serves, the grant it needs."""

    tool_id: str
    capability: str
    required_grant: str


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ContextSlice:
    objective_digest: str
    mission_digest: str
    sections: tuple[tuple[str, str], ...]  # (name, canonical JSON payload)
    dropped: tuple[str, ...]
    byte_size: int
    digest: str

    def section(self, name: str) -> Any:
        for present, payload in self.sections:
            if present == name:
                return json.loads(payload)
        raise KeyError(name)

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.sections)


def as_memory_data(records: Sequence[MemoryRecord], *, objective_digest: str,
                   limit: int = MAX_MEMORY_RECORDS) -> tuple[dict, ...]:
    """Label memory so it cannot be mistaken for policy or read as authority.

    Every entry is stamped MEMORY_DATA with authority "none" and its provenance.
    Nothing downstream reads these entries when computing capabilities, so a
    record that claims a permission grants exactly nothing.
    """
    if type(records) not in (list, tuple):
        _refuse("memory: bounded sequence required")
    if type(limit) is not int or limit < 0:
        _refuse("memory limit: non-negative integer required")
    labelled: list[dict] = []
    for record in records:
        if type(record) is not MemoryRecord:
            _refuse("memory: MemoryRecord instances required")
        if record.objective_digest != objective_digest:
            _refuse("unscoped memory refused: record belongs to another objective")
        labelled.append({
            "label": MEMORY_LABEL,
            "authority": MEMORY_AUTHORITY,
            "memory_id": _text(record.memory_id, "memory_id"),
            "provenance": _text(record.provenance, "provenance"),
            "observed_at": _finite(record.observed_at, "observed_at"),
            "text": record.text if type(record.text) is str else _text(record.text, "text"),
        })
    # Deterministic: newest first, ties broken by id, then bounded.
    labelled.sort(key=lambda entry: (-entry["observed_at"], entry["memory_id"]))
    return tuple(labelled[:limit])


def select_capabilities(objective: ObjectiveSpec, mission: MissionIR,
                        registry: Sequence[ToolEntry], *,
                        grants: Sequence[str] = (),
                        max_tools: int = MAX_TOOLS) -> CapabilitySelection:
    """Objective -> Mission IR -> capability graph -> capabilities -> tools.

    Narrowing only. The authorized envelope is the objective's permission_refs
    intersected with the grants currently held; a registry row outside it, or
    serving a capability the mission never asked for, is excluded with a reason.
    """
    if type(objective) is not ObjectiveSpec or type(mission) is not MissionIR:
        _refuse("validated ObjectiveSpec and MissionIR required")
    if type(registry) not in (list, tuple):
        _refuse("registry: bounded sequence required")
    if type(grants) not in (list, tuple, frozenset, set):
        _refuse("grants: explicit collection required")
    if type(max_tools) is not int or not 0 < max_tools <= MAX_TOOLS:
        _refuse(f"max_tools: 1..{MAX_TOOLS} required")

    spec = objective.to_dict()
    envelope = frozenset(spec["permission_refs"]) & frozenset(str(g) for g in grants)
    requested: set[str] = set()
    for effect in mission.to_dict()["effects"]:
        requested.update(effect["capabilities"])

    kept: list[ToolEntry] = []
    excluded: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in registry:
        if type(entry) is not ToolEntry:
            _refuse("registry: ToolEntry instances required")
        if entry.tool_id in seen:
            _refuse("registry: duplicate tool id")
        seen.add(entry.tool_id)
        if entry.capability not in requested:
            excluded.append((entry.tool_id, "capability_not_requested_by_mission"))
        elif entry.required_grant not in envelope:
            excluded.append((entry.tool_id, "no_current_grant"))
        else:
            kept.append(entry)

    kept.sort(key=lambda e: (e.capability, e.tool_id))
    if len(kept) > max_tools:
        for entry in kept[max_tools:]:
            excluded.append((entry.tool_id, "tool_budget_exhausted"))
        kept = kept[:max_tools]
    capabilities = tuple(sorted({e.capability for e in kept}))
    # Structural proof of narrowing (not an assert: it must hold under -O too).
    if not set(capabilities) <= requested or not {e.required_grant for e in kept} <= envelope:
        _refuse("capability selection widened the authorized set")
    excluded.sort()
    return CapabilitySelection(capabilities, tuple(e.tool_id for e in kept), tuple(excluded))


def _latest_per_source(observations: Sequence[Mapping[str, Any]], *,
                       objective_digest: str, known_sources: frozenset[str],
                       max_observations: int) -> tuple[dict, ...]:
    latest: dict[str, Mapping[str, Any]] = {}
    for obs in observations:
        if type(obs) is not dict:
            _refuse("observations: JSON objects required")
        digest = obs.get("objective_digest")
        if digest != objective_digest:
            _refuse("observation refused: belongs to another objective")
        source = obs.get("source_ref")
        if type(source) is not str or source not in known_sources:
            _refuse("observation refused: source is not part of this objective")
        seen_at = _finite(obs.get("observed_at"), "observed_at")
        current = latest.get(source)
        if current is None:
            latest[source] = obs
            continue
        key = (seen_at, str(obs.get("observation_id", "")))
        rival = (_finite(current.get("observed_at"), "observed_at"), str(current.get("observation_id", "")))
        if key > rival:  # deterministic tie-break on observation_id
            latest[source] = obs
    ordered = [latest[ref] for ref in sorted(latest)]
    # Older observations per source are dropped outright — not summarized.
    return tuple(json.loads(_canonical(obs)) for obs in ordered[:max_observations])


def _world_state(facts: Sequence[WorldFact], *, now: float) -> tuple[dict, ...]:
    rendered: list[dict] = []
    for fact in facts:
        if type(fact) is not WorldFact:
            _refuse("world_state: WorldFact instances required")
        age = now - _finite(fact.observed_at, "observed_at")
        fresh = 0.0 <= age <= _finite(fact.max_age_seconds, "max_age_seconds")
        rendered.append({
            "fact_id": _text(fact.fact_id, "fact_id"),
            "source_ref": _text(fact.source_ref, "source_ref"),
            "scope_ref": _text(fact.scope_ref, "scope_ref"),
            "provenance": _text(fact.provenance, "provenance"),
            "observed_at": float(fact.observed_at),
            # Stale is never quietly presented as current fact.
            "value": fact.value if fresh else UNKNOWN,
            "freshness": "fresh" if fresh else UNKNOWN,
        })
    rendered.sort(key=lambda entry: entry["fact_id"])
    return tuple(rendered)


def context_digest(sections: Sequence[tuple[str, str]], dropped: Sequence[str]) -> str:
    """Digest of exactly what the model will see, plus what was dropped."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sections": [[name, body] for name, body in sections],
        "dropped": list(dropped),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def assemble_v5_context(*, spec: ObjectiveSpec, mission: MissionIR, lifecycle: str,
                        policy: Mapping[str, Any], now: float,
                        observations: Sequence[Mapping[str, Any]] = (),
                        enrolled_sources: Sequence[str] = (),
                        world_facts: Sequence[WorldFact] = (),
                        memory: Sequence[MemoryRecord] = (),
                        skill: SkillCandidate | None = None,
                        registry: Sequence[ToolEntry] = (),
                        grants: Sequence[str] = (),
                        max_observations: int = MAX_OBSERVATIONS,
                        max_tools: int = MAX_TOOLS,
                        byte_budget: int = DEFAULT_BYTE_BUDGET,
                        extra: Mapping[str, Any] | None = None) -> ContextSlice:
    """Assemble the only context a V5 execution call is allowed to receive.

    Raises ContextRefusal rather than including anything out of scope, and drops
    whole trailing sections (never the policy head) when the byte budget binds.
    """
    if type(spec) is not ObjectiveSpec or type(mission) is not MissionIR:
        _refuse("validated ObjectiveSpec and MissionIR required")
    _finite(now, "now")
    if type(lifecycle) is not str:
        _refuse("lifecycle: text required")
    if type(policy) is not dict or not policy:
        _refuse("applicable policy required")
    if type(max_observations) is not int or not 0 < max_observations <= MAX_OBSERVATIONS:
        _refuse(f"max_observations: 1..{MAX_OBSERVATIONS} required")
    if type(byte_budget) is not int or byte_budget <= 0:
        _refuse("byte_budget: positive integer required")
    if extra is not None:
        if type(extra) is not dict:
            _refuse("extra: mapping required")
        for key in sorted(extra):
            if key in FORBIDDEN_EXTRA_KEYS:
                _refuse(f"refused: '{key}' would dump unscoped context into the model call")
            _refuse(f"refused: unknown context key '{key}' has no bounded section")

    data = spec.to_dict()
    known_sources = frozenset(source["source_ref"] for source in data["sources"])
    if not frozenset(str(ref) for ref in enrolled_sources) <= known_sources:
        _refuse("enrollment refused: source outside this objective")

    kept_observations = _latest_per_source(
        observations, objective_digest=spec.digest, known_sources=known_sources,
        max_observations=max_observations)
    relevant_sources = ({obs["source_ref"] for obs in kept_observations}
                        | {str(ref) for ref in enrolled_sources})
    predicates = tuple(
        {"predicate_id": p["predicate_id"], "source_ref": p["source_ref"], "field": p["field"],
         "operator": p["operator"], "expected": p["expected"], "value_type": p["value_type"]}
        for p in sorted(data["predicates"], key=lambda p: p["predicate_id"])
        if p["source_ref"] in relevant_sources)

    selection = select_capabilities(spec, mission, registry, grants=grants, max_tools=max_tools)
    skill_payload: dict | None = None
    if skill is not None:
        if type(skill) is not SkillCandidate:
            _refuse("skill: SkillCandidate required")
        if _finite(skill.confidence, "confidence") >= SKILL_CONFIDENCE_THRESHOLD:
            skill_payload = {"skill_id": skill.skill_id, "digest": skill.digest,
                             "confidence": float(skill.confidence)}

    mission_data = mission.to_dict()
    payloads: dict[str, Any] = {
        "policy": {"lifecycle": lifecycle, "objective_digest": spec.digest,
                   "owner_id": data["owner_id"], "scope_id": data["scope_id"],
                   "policy": json.loads(_canonical(policy)),
                   "memory_is_not_authority": True},
        "mission": {"mission_id": mission_data["mission_id"], "goal": mission_data["goal"],
                    "digest": mission.digest, "privacy": mission_data["privacy"],
                    "risk": mission_data["risk"], "side_effect": mission_data["side_effect"]},
        "objective_predicates": list(predicates),
        "world_state": list(_world_state(world_facts, now=now)),
        "observations": list(kept_observations),
        "capabilities": {"capabilities": list(selection.capabilities),
                         "tools": list(selection.tools),
                         "excluded": [list(pair) for pair in selection.excluded]},
        "skill": skill_payload,
        "memory": list(as_memory_data(memory, objective_digest=spec.digest)),
    }

    rendered = [(name, _canonical(payloads[name])) for name in SECTION_ORDER
                if payloads[name] not in (None, [], {})]
    head_bytes = sum(len(body.encode("utf-8")) + len(name) for name, body in rendered
                     if name in HEAD_SECTIONS)
    if head_bytes > byte_budget:
        # Fail closed: a sliced policy head is worse than no call at all.
        _refuse("byte_budget cannot hold the policy/invariant head")

    kept: list[tuple[str, str]] = []
    dropped: list[str] = []
    total = 0
    binding = False
    for name, body in rendered:
        size = len(body.encode("utf-8")) + len(name)
        if name in HEAD_SECTIONS:
            kept.append((name, body))
            total += size
            continue
        # Once the budget binds, every remaining (lower priority) section goes:
        # the tail is dropped whole, never partially, and never reordered.
        if binding or total + size > byte_budget:
            binding = True
            dropped.append(name)
            continue
        kept.append((name, body))
        total += size
    return ContextSlice(objective_digest=spec.digest, mission_digest=mission.digest,
                        sections=tuple(kept), dropped=tuple(dropped), byte_size=total,
                        digest=context_digest(kept, dropped))
