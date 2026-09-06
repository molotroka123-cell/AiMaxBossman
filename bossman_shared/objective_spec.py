"""V5 candidate contracts and pure observation evaluation; NOT a runtime.

No observer, enrollment, migration, scheduler, model call or admission authority
is installed here. N0 and V4 M0 remain release gates. Callers must authenticate
observations and load current lifecycle/enrollment from the existing canonical
store before using projections. A proposal candidate is never an execution grant.
The JSON/digest boundary deliberately follows MissionIR's immutable convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any

from .mission_ir import (
    MAX_BYTES, MissionIRValidationError, _canonical, _json_types, _keys,
    _number, _string, _strings, _unique_object, _utf8_size,
)


class ObjectiveValidationError(ValueError):
    """Malformed candidate or observation; structural checks are not policy."""


_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
LIFECYCLES = frozenset({"DRAFT", "ACTIVE", "PAUSED", "EXPIRED", "REVOKED"})
_FIELDS = frozenset({
    "schema_version", "owner_id", "scope_id", "objective_id", "revision",
    "previous_digest", "predicates", "sources", "expires_at", "priority",
    "allowed_triggers", "permission_refs", "conflict_keys", "cooldown_seconds",
    "limits", "stop_conditions",
})


def _fail(message: str) -> None:
    raise ObjectiveValidationError(message)


def _validate(raw: dict) -> None:
    _keys(raw, _FIELDS, "objective")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        _fail("unsupported objective schema")
    for name in ("owner_id", "scope_id", "objective_id"):
        _string(raw[name], name)
    _number(raw["revision"], "revision", positive=True, integer=True)
    _number(raw["expires_at"], "expires_at", positive=True)
    _number(raw["priority"], "priority", integer=True)
    _number(raw["cooldown_seconds"], "cooldown_seconds")
    for name in ("permission_refs", "conflict_keys", "stop_conditions", "allowed_triggers"):
        _strings(raw[name], name, nonempty=True)
    if not set(raw["allowed_triggers"]) <= {"source_change", "scheduled", "owner_request"}:
        _fail("unsupported trigger")
    limits = _keys(raw["limits"], {"max_observations", "max_missions", "max_wall_seconds", "max_cost_usd"}, "limits")
    for name, value in limits.items():
        _number(value, name, integer=name in {"max_observations", "max_missions"})
    sources = raw["sources"]
    predicates = raw["predicates"]
    for name, value in (("sources", sources), ("predicates", predicates)):
        if type(value) is not list or not 1 <= len(value) <= 128:
            _fail(f"{name}: 1..128 items required")
    refs = set()
    for source in sources:
        _keys(source, {"source_ref", "source_revision", "max_age_seconds"}, "source")
        for name in ("source_ref", "source_revision"):
            _string(source[name], name)
        _number(source["max_age_seconds"], "max_age_seconds", positive=True)
        if source["max_age_seconds"] > 86400:
            _fail("freshness exceeds 86400 seconds")
        if source["source_ref"] in refs:
            _fail("duplicate source")
        refs.add(source["source_ref"])
    ids = set()
    for predicate in predicates:
        _keys(predicate, {"predicate_id", "source_ref", "field", "value_type", "operator", "expected"}, "predicate")
        for name in ("predicate_id", "source_ref", "field", "value_type", "operator"):
            _string(predicate[name], name)
        if predicate["predicate_id"] in ids or predicate["source_ref"] not in refs:
            _fail("duplicate predicate or unknown source")
        ids.add(predicate["predicate_id"])
        if predicate["value_type"] not in {"boolean", "number", "text"}:
            _fail("unsupported predicate type")
        if predicate["operator"] not in {"eq", "ge", "le"}:
            _fail("unsupported predicate operator")
        if predicate["value_type"] != "number" and predicate["operator"] != "eq":
            _fail("ordered comparison requires number")
        if not _typed(predicate["expected"], predicate["value_type"]):
            _fail("expected value has wrong type or is not finite")


def _typed(value: Any, kind: str) -> bool:
    if kind == "number":
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    if kind == "boolean":
        return type(value) is bool
    return type(value) is str and "\x00" not in value


@dataclass(frozen=True, slots=True, init=False)
class ObjectiveSpec:
    """Immutable candidate; changes bind to a trusted prior revision."""

    _json: str

    def __init__(self) -> None:
        raise TypeError("use ObjectiveSpec.from_dict/from_json")

    @classmethod
    def from_dict(cls, raw: dict, *, previous: ObjectiveSpec | None = None) -> ObjectiveSpec:
        try:
            _json_types(raw)
            canonical = _canonical(raw)
            data = json.loads(canonical)
            _validate(data)
            if data["revision"] == 1:
                if data["previous_digest"] is not None or previous is not None:
                    _fail("initial objective cannot replace a previous revision")
            else:
                if type(previous) is not ObjectiveSpec:
                    _fail("trusted previous revision required")
                old = previous.to_dict()
                if data["revision"] != old["revision"] + 1 or data["previous_digest"] != previous.digest:
                    _fail("revision chain mismatch")
                if any(data[key] != old[key] for key in ("owner_id", "scope_id", "objective_id")):
                    _fail("revision changes identity")
            instance = object.__new__(cls)
            object.__setattr__(instance, "_json", canonical)
            return instance
        except MissionIRValidationError as exc:
            raise ObjectiveValidationError(str(exc)) from exc

    @classmethod
    def from_json(cls, value: str, *, previous: ObjectiveSpec | None = None) -> ObjectiveSpec:
        try:
            if type(value) is not str or _utf8_size(value) > MAX_BYTES:
                _fail("invalid or oversized objective JSON")
            raw = json.loads(value, object_pairs_hook=_unique_object,
                             parse_constant=lambda _: _fail("nonfinite JSON value"))
            return cls.from_dict(raw, previous=previous)
        except (ValueError, RecursionError) as exc:
            raise ObjectiveValidationError("invalid objective JSON") from exc

    @classmethod
    def from_trusted_json(cls, value: str, *, digest: str) -> ObjectiveSpec:
        """Rehydrate a spec a durable store already accepted, binding its digest.

        A revision above 1 can only be built with its predecessor in hand, which
        is the right rule at write time and an impossible one at read time: a
        store that does not retain every ancestor would find its own revised
        objectives permanently unreadable. Rather than let each caller reach
        past the constructor to work around that, the invariant is stated here.

        What is re-run is the structural validation, against the stored bytes
        with the chain fields normalized away. What replaces the chain rule is
        the caller's recorded digest: the bytes must still hash to it, so
        tampered storage is refused instead of served as canonical. This proves
        the record is the one that was validated; it does not re-derive that the
        revision chain was ever sound, which is why only a store that verified
        the chain on write may call it.
        """
        if type(digest) is not str or not _HEX_DIGEST.fullmatch(digest):
            _fail("trusted rehydration requires the recorded lowercase digest")
        if type(value) is not str or _utf8_size(value) > MAX_BYTES:
            _fail("invalid or oversized objective JSON")
        if hashlib.sha256(value.encode("utf-8")).hexdigest() != digest:
            _fail("stored objective bytes do not match their recorded digest")
        try:
            raw = json.loads(value, object_pairs_hook=_unique_object,
                             parse_constant=lambda _: _fail("nonfinite JSON value"))
        except (ValueError, RecursionError) as exc:
            raise ObjectiveValidationError("invalid objective JSON") from exc
        if type(raw) is not dict:
            _fail("objective: JSON object required")
        # The chain fields are the only ones a predecessor could contradict, so
        # they are normalized out before structural validation and left intact
        # in the bytes the digest covers.
        cls.from_dict({**raw, "revision": 1, "previous_digest": None})
        instance = object.__new__(cls)
        object.__setattr__(instance, "_json", value)
        if instance.digest != digest:
            _fail("stored objective bytes do not match their recorded digest")
        return instance

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return json.loads(self._json)

    def to_json(self) -> str:
        return self._json


@dataclass(frozen=True, slots=True)
class Evaluation:
    lifecycle: str
    condition: str
    predicate_results: tuple[tuple[str, str, str], ...]
    proposal_candidate: bool
    # This pure foundation can NEVER authorize work, even for an ACTIVE input.
    admission_allowed: bool = field(default=False, init=False)


def evaluate(spec: ObjectiveSpec, observations: list[dict], *, now: float,
             lifecycle: str = "DRAFT", enrolled_sources: tuple[str, ...] = (),
             trigger: str | None = None) -> Evaluation:
    """Evaluate one bounded observation batch; UNKNOWN dominates aggregation.

    Observation identity binds owner, scope, objective digest and source revision.
    Conflicting/duplicate source observations are UNKNOWN, never last-write-wins.
    Quotas here bound this batch only; cumulative usage/cooldown/current grants
    belong to future canonical-ledger integration, not this projection.
    """
    try:
        _number(now, "now")
        if type(lifecycle) is not str or lifecycle not in LIFECYCLES:
            _fail("unsupported lifecycle")
        if type(spec) is not ObjectiveSpec:
            _fail("validated ObjectiveSpec required")
        if type(enrolled_sources) is not tuple:
            _fail("explicit enrollment tuple required")
        _strings(list(enrolled_sources), "enrolled_sources")
        if trigger is not None:
            _string(trigger, "trigger")
        if type(observations) is not list or len(observations) > 128:
            _fail("bounded observation batch required")
        _json_types(observations)
        observations = json.loads(_canonical({"observations": observations}))["observations"]
        data = spec.to_dict()
        sources = {s["source_ref"]: s for s in data["sources"]}
        if not set(enrolled_sources) <= set(sources):
            _fail("enrollment outside objective scope")
        effective = lifecycle
        if lifecycle != "REVOKED" and now >= data["expires_at"]:
            effective = "EXPIRED"
        quota_exceeded = len(observations) > data["limits"]["max_observations"]
        results = []
        observation_ids = [o.get("observation_id") for o in observations if type(o) is dict]
        for predicate in data["predicates"]:
            ref = predicate["source_ref"]
            matches = [o for o in observations if type(o) is dict and o.get("source_ref") == ref]
            reason = ""
            if ref not in enrolled_sources:
                reason = "not_enrolled"
            elif quota_exceeded:
                reason = "batch_quota_exceeded"
            elif len(matches) != 1:
                reason = "missing_or_ambiguous_source"
            else:
                obs = matches[0]
                fields = {"observation_id", "owner_id", "scope_id", "objective_digest", "source_ref", "source_revision", "observed_at", "values"}
                if set(obs) != fields or type(obs["observation_id"]) is not str or not obs["observation_id"].strip():
                    reason = "malformed_observation"
                elif (obs["observation_id"] != obs["observation_id"].strip()
                      or "\x00" in obs["observation_id"]
                      or observation_ids.count(obs["observation_id"]) != 1):
                    reason = "ambiguous_observation_identity"
                elif any(obs[k] != data[k] for k in ("owner_id", "scope_id")) or obs["objective_digest"] != spec.digest:
                    reason = "identity_mismatch"
                elif obs["source_revision"] != sources[ref]["source_revision"]:
                    reason = "source_revision_mismatch"
                elif not _typed(obs["observed_at"], "number") or not 0 <= obs["observed_at"] <= now:
                    reason = "invalid_observation_time"
                elif now - obs["observed_at"] > sources[ref]["max_age_seconds"]:
                    reason = "stale"
                elif type(obs["values"]) is not dict or predicate["field"] not in obs["values"]:
                    reason = "missing_value"
                elif not _typed(obs["values"][predicate["field"]], predicate["value_type"]):
                    reason = "invalid_value_type"
            if reason:
                results.append((predicate["predicate_id"], "UNKNOWN", reason))
                continue
            value, expected = obs["values"][predicate["field"]], predicate["expected"]
            operator = predicate["operator"]
            satisfied = value == expected if operator == "eq" else value >= expected if operator == "ge" else value <= expected
            results.append((predicate["predicate_id"], "SATISFIED" if satisfied else "DEVIATED", "fresh_observation"))
        conditions = {r[1] for r in results}
        condition = "UNKNOWN" if "UNKNOWN" in conditions else "DEVIATED" if "DEVIATED" in conditions else "SATISFIED"
        candidate = (effective == "ACTIVE" and condition == "DEVIATED"
                     and trigger in data["allowed_triggers"] and data["limits"]["max_missions"] > 0)
        return Evaluation(effective, condition, tuple(results), candidate)
    except MissionIRValidationError as exc:
        raise ObjectiveValidationError(str(exc)) from exc


def transition_lifecycle(spec: ObjectiveSpec, current: str, requested: str, *,
                         now: float, owner_id: str, scope_id: str) -> str:
    """Pure transition validation, never authorization or persistence.

    The service must resolve the authenticated owner and current lifecycle from
    its canonical store and commit with compare-and-swap. Supplying an owner ID
    here does not authenticate a caller. Import stays DRAFT; activation requires
    a separate current-policy/enrollment gate at integration.
    """
    try:
        if type(spec) is not ObjectiveSpec:
            _fail("validated ObjectiveSpec required")
        _number(now, "now")
        for state in (current, requested):
            if type(state) is not str or state not in LIFECYCLES:
                _fail("unsupported lifecycle")
        data = spec.to_dict()
        if owner_id != data["owner_id"] or scope_id != data["scope_id"]:
            _fail("lifecycle identity mismatch")
        # Revocation is sticky, including across expiry. Expiry never resurrects.
        effective = "EXPIRED" if current != "REVOKED" and now >= data["expires_at"] else current
        allowed = {
            "DRAFT": {"DRAFT", "ACTIVE", "PAUSED", "REVOKED"},
            "ACTIVE": {"ACTIVE", "PAUSED", "REVOKED"},
            "PAUSED": {"PAUSED", "ACTIVE", "REVOKED"},
            "EXPIRED": {"EXPIRED", "REVOKED"},
            "REVOKED": {"REVOKED"},
        }
        if requested not in allowed[effective]:
            _fail(f"forbidden lifecycle transition {effective} -> {requested}")
        return requested
    except MissionIRValidationError as exc:
        raise ObjectiveValidationError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ProposalProjection:
    """Deterministic dedup material only; not a mission or execution grant."""

    proposal_id: str
    objective_digest: str
    observation_digests: tuple[str, ...]
    valid_until: float
    admission_allowed: bool = field(default=False, init=False)


def project_proposal(spec: ObjectiveSpec, observations: list[dict], *, now: float,
                     snapshot: dict, trigger: str) -> ProposalProjection | None:
    """Project a proposal from a *caller-resolved* canonical ledger snapshot.

    No store is created and no counters are advanced. Snapshot authentication,
    atomic dedup/reservation, grants, conflict keys and stop-condition resolution
    remain the existing service's responsibility. Returned IDs bind source
    content and objective revision, independent of trigger or list ordering;
    they do not prove freshness at future admission/effect boundaries.

    Usage is cumulative across objective revisions, including reserved work;
    integration must not reset counters when changing the spec. `stopped` is the
    canonical resolution of owner stop conditions, not model interpretation.
    """
    try:
        if type(spec) is not ObjectiveSpec:
            _fail("validated ObjectiveSpec required")
        _json_types(snapshot)
        state = json.loads(_canonical(snapshot))
        _keys(state, {"owner_id", "scope_id", "objective_digest", "lifecycle",
                      "enrolled_sources", "observations_used", "missions_used",
                      "wall_seconds_used", "cost_usd_used", "last_proposal_at", "stopped"}, "snapshot")
        data = spec.to_dict()
        if any(state[k] != data[k] for k in ("owner_id", "scope_id")) or state["objective_digest"] != spec.digest:
            _fail("snapshot identity mismatch")
        if type(state["stopped"]) is not bool:
            _fail("canonical stop condition required")
        _strings(state["enrolled_sources"], "enrolled_sources")
        for name in ("observations_used", "missions_used", "wall_seconds_used", "cost_usd_used"):
            _number(state[name], name, integer=name in {"observations_used", "missions_used"})
        _number(now, "now")
        last = state["last_proposal_at"]
        if last is not None:
            _number(last, "last_proposal_at")
            if last > now:
                _fail("future proposal timestamp")
        # Snapshot the full batch before evaluation and hashing to bind precisely
        # the content that was evaluated, even if callers later mutate inputs.
        _json_types(observations)
        batch = json.loads(_canonical({"observations": observations}))["observations"]
        result = evaluate(spec, batch, now=now, lifecycle=state["lifecycle"],
                          enrolled_sources=tuple(state["enrolled_sources"]), trigger=trigger)
        if not result.proposal_candidate or state["stopped"]:
            return None
        limits = data["limits"]
        if (state["observations_used"] + len(batch) > limits["max_observations"]
                or state["missions_used"] >= limits["max_missions"]
                or state["wall_seconds_used"] >= limits["max_wall_seconds"]
                or state["cost_usd_used"] > limits["max_cost_usd"]
                or last is not None and now - last < data["cooldown_seconds"]):
            return None
        # A zero remaining money budget can still propose a zero-cost local
        # mission; the authoritative ledger must reserve its actual cost.
        sources = {s["source_ref"]: s for s in data["sources"]}
        used = {p["source_ref"] for p in data["predicates"]}
        relevant = [o for o in batch if type(o) is dict and o.get("source_ref") in used]
        digests = tuple(sorted(hashlib.sha256(_canonical(o).encode("utf-8")).hexdigest() for o in relevant))
        expiry = min(data["expires_at"], *(o["observed_at"] + sources[o["source_ref"]]["max_age_seconds"] for o in relevant))
        material = _canonical({"objective_digest": spec.digest, "observations": list(digests)})
        return ProposalProjection(hashlib.sha256(material.encode("utf-8")).hexdigest(), spec.digest, digests, expiry)
    except MissionIRValidationError as exc:
        raise ObjectiveValidationError(str(exc)) from exc
