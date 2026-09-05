"""Epoch 4 immutable *candidate* mission contract, with no execution authority.

Validation is server-side structural validation, never authorization. Scope and
reservation references are untrusted identifiers to resolve against existing
policy/ledgers. No receipt, grant, signature, dispatcher or completion writer is
created here. BCC finalization and V3 journals remain authoritative.

Version 1 intentionally supports only file/app/process post-state
contracts. Other verifier kinds require a reviewed schema/adapter extension.
Revisions are append-only obligations: replacing/removing an existing effect is
not supported. Callers must load the trusted prior revision before accepting a
revision > 1; a predecessor hash supplied by a model alone is insufficient.
A changed revision is only a candidate: downstream admission must reauthorize
its digest and enforce the existing privacy floor before any use or dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping

SCHEMA_VERSION = 1
EFFECT_KINDS = frozenset({"READ_ONLY", "IDEMPOTENT_WRITE", "REVERSIBLE_WRITE", "IRREVERSIBLE"})
VERIFIER_KINDS = frozenset({"file", "app", "process"})
MAX_BYTES = 1_048_576
MAX_EFFECTS = 1000
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset({"schema_version", "owner_id", "project_id", "mission_id", "goal_id",
                     "revision", "previous_digest", "goal", "side_effect", "effects", "privacy", "risk",
                     "authorized_scope_refs", "budget", "reservation_refs", "recovery",
                     "success_conditions", "artifact_refs", "provenance"})


class MissionIRValidationError(ValueError):
    """A candidate cannot cross the structural contract boundary."""


def _fail(message: str) -> None:
    raise MissionIRValidationError(message)


def _keys(value: Any, fields: set[str] | frozenset[str], path: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        _fail(f"{path}: exact fields required: {', '.join(sorted(fields))}")
    return value


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise MissionIRValidationError("invalid Unicode: UTF-8 text required") from exc


def _string(value: Any, path: str, *, empty: bool = False) -> None:
    if type(value) is not str or (not empty and not value.strip()) or value != value.strip() or "\x00" in value:
        _fail(f"{path}: nonempty canonical string required")


def _number(value: Any, path: str, *, positive: bool = False, integer: bool = False) -> None:
    if type(value) not in ((int,) if integer else (int, float)):
        _fail(f"{path}: finite nonnegative number required")
    try:
        valid = math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        _fail(f"{path}: finite {'positive' if positive else 'nonnegative'} number required")


def _strings(value: Any, path: str, *, nonempty: bool = False) -> None:
    if type(value) is not list or (nonempty and not value):
        _fail(f"{path}: {'nonempty ' if nonempty else ''}array required")
    for item in value:
        _string(item, path)
    if len(set(value)) != len(value):
        _fail(f"{path}: duplicate entries")


def _verifier(v: Any) -> None:
    _keys(v, {"kind", "target", "expect", "max_age_seconds"}, "verifier")
    if type(v["kind"]) is not str or v["kind"] not in VERIFIER_KINDS:
        _fail("unsupported verifier kind")
    _string(v["target"], "verifier.target")
    _number(v["max_age_seconds"], "verifier.max_age_seconds", positive=True)
    if v["max_age_seconds"] > 86400:
        _fail("verifier freshness exceeds 86400 seconds")
    e = v["expect"]
    if type(e) is not dict or not e:
        _fail("verifier.expect: nonempty object required")
    kind = v["kind"]
    allowed = {"file": {"exists", "sha256", "contains", "min_bytes"},
               "app": {"running"}, "process": {"running"}}[kind]
    if not set(e) <= allowed:
        _fail("unsupported expectation field")
    for key, value in e.items():
        if key in {"exists", "running"}:
            if type(value) is not bool:
                _fail(f"{key}: boolean required")
        elif key == "min_bytes":
            _number(value, key, positive=True, integer=True)
        elif key == "sha256":
            if type(value) is not str or not _HEX.fullmatch(value):
                _fail("sha256: lowercase full digest required")
        else:
            _string(value, key)
    if kind == "file" and e.get("exists") is False and set(e) != {"exists"}:
        _fail("file absence cannot ignore content obligations")
    if kind == "process" and not re.fullmatch(r"[1-9][0-9]*", v["target"]):
        _fail("process target must be a positive pid")


def _validate(raw: dict) -> None:
    _keys(raw, _FIELDS, "mission")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported mission schema version")
    for key in ("owner_id", "project_id", "mission_id", "goal_id", "goal"):
        _string(raw[key], key)
    _number(raw["revision"], "revision", positive=True, integer=True)
    prior = raw["previous_digest"]
    if raw["revision"] == 1:
        if prior is not None:
            _fail("initial revision cannot have predecessor")
    elif type(prior) is not str or not _HEX.fullmatch(prior):
        _fail("revision requires full predecessor digest")
    if type(raw["side_effect"]) is not bool:
        _fail("side_effect: boolean required")
    for key, choices in (("privacy", {"private", "local_only", "internal", "public"}),
                         ("risk", {"low", "medium", "high"})):
        if type(raw[key]) is not str or raw[key] not in choices:
            _fail(f"unsupported {key}")
    for key in ("authorized_scope_refs", "reservation_refs", "artifact_refs"):
        _strings(raw[key], key)
    _strings(raw["success_conditions"], "success_conditions", nonempty=True)
    budget = _keys(raw["budget"], {"max_cost_usd", "max_tokens", "max_wall_seconds"}, "budget")
    for key, value in budget.items():
        _number(value, f"budget.{key}", integer=key == "max_tokens")
    recovery = _keys(raw["recovery"], {"max_attempts_per_effect", "max_attempts_total"}, "recovery")
    for key, value in recovery.items():
        _number(value, key, integer=True)
    if recovery["max_attempts_per_effect"] > 3 or recovery["max_attempts_total"] > 10:
        _fail("recovery exceeds initial epoch bounds (3 per effect / 10 total)")
    if recovery["max_attempts_per_effect"] > recovery["max_attempts_total"]:
        _fail("per-effect recovery exceeds mission total")
    provenance = _keys(raw["provenance"], {"source", "source_ref"}, "provenance")
    for key, value in provenance.items():
        _string(value, f"provenance.{key}")
    effects = raw["effects"]
    if type(effects) is not list or len(effects) > MAX_EFFECTS:
        _fail("effects: bounded array required")
    if raw["side_effect"] and not effects:
        _fail("effectful mission has no obligations")
    by_id: dict[str, dict] = {}
    for effect in effects:
        _keys(effect, {"effect_id", "kind", "description", "capabilities", "depends_on", "verifiers"}, "effect")
        for key in ("effect_id", "description"):
            _string(effect[key], f"effect.{key}")
        if effect["effect_id"] in by_id:
            _fail("duplicate effect id")
        by_id[effect["effect_id"]] = effect
        if type(effect["kind"]) is not str or effect["kind"] not in EFFECT_KINDS:
            _fail("unsupported effect kind")
        if not raw["side_effect"] and effect["kind"] != "READ_ONLY":
            _fail("informational mission contains mutating effect")
        _strings(effect["capabilities"], "effect.capabilities", nonempty=True)
        _strings(effect["depends_on"], "effect.depends_on")
        if type(effect["verifiers"]) is not list or not effect["verifiers"]:
            _fail("every declared effect requires independent post-state obligations")
        for verifier in effect["verifiers"]:
            _verifier(verifier)
    # Iterative Kahn traversal avoids recursion limits on adversarial deep DAGs.
    incoming = {key: len(e["depends_on"]) for key, e in by_id.items()}
    outgoing: dict[str, list[str]] = {key: [] for key in by_id}
    for key, e in by_id.items():
        for dep in e["depends_on"]:
            if dep not in by_id:
                _fail(f"missing dependency: {dep}")
            outgoing[dep].append(key)
    ready = [key for key, count in incoming.items() if count == 0]
    count = 0
    while ready:
        key = ready.pop()
        count += 1
        for child in outgoing[key]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if count != len(by_id):
        _fail("effect dependency cycle")


def _canonical(raw: Mapping[str, Any]) -> str:
    try:
        text = json.dumps(raw, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise MissionIRValidationError("mission must contain finite JSON values") from exc
    if len(text.encode("utf-8")) > MAX_BYTES:
        _fail("mission exceeds size limit")
    return text


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True, init=False)
class MissionIR:
    """Deeply immutable identity. Exported dictionaries are detached copies.

    Use from_dict/from_json; direct construction is intentionally unavailable.
    Hashes bind obligations, not execution attempts or permission grants.
    """

    _json: str

    def __init__(self) -> None:
        raise TypeError("use MissionIR.from_dict or MissionIR.from_json")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, previous: MissionIR | None = None) -> MissionIR:
        if type(raw) is not dict:
            _fail("mission: JSON object required")
        # JSON roundtrip isolates caller mutation; reject non-JSON input *before*
        # the roundtrip can silently coerce integer keys/tuples.
        _json_types(raw)
        canonical = _canonical(raw)
        snapshot = json.loads(canonical)
        _validate(snapshot)
        if snapshot["revision"] > 1:
            if type(previous) is not MissionIR:
                _fail("trusted previous revision required")
            old = previous.to_dict()
            if snapshot["revision"] != old["revision"] + 1 or snapshot["previous_digest"] != previous.digest:
                _fail("revision chain mismatch")
            for key in ("owner_id", "project_id", "mission_id", "goal_id"):
                if snapshot[key] != old[key]:
                    _fail(f"revision changes {key}")
            new_effects = {e["effect_id"]: e for e in snapshot["effects"]}
            if any(new_effects.get(e["effect_id"]) != e for e in old["effects"]):
                _fail("revision removes or alters prior effect obligations")
        elif previous is not None:
            _fail("initial revision cannot replace a previous mission")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_json", canonical)
        return instance

    @classmethod
    def from_json(cls, value: str, *, previous: MissionIR | None = None) -> MissionIR:
        if type(value) is not str or _utf8_size(value) > MAX_BYTES:
            _fail("mission JSON exceeds size limit or is not text")
        try:
            raw = json.loads(value, object_pairs_hook=_unique_object,
                             parse_constant=lambda _: _fail("nonfinite JSON value"))
        except (ValueError, RecursionError) as exc:
            raise MissionIRValidationError("invalid mission JSON") from exc
        return cls.from_dict(raw, previous=previous)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._json.encode("utf-8")).hexdigest()

    @property
    def mission_id(self) -> str:
        return self.to_dict()["mission_id"]

    @property
    def revision(self) -> int:
        return self.to_dict()["revision"]

    def to_json(self) -> str:
        return self._json

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    def required_effects(self) -> list[dict[str, Any]]:
        """Detached ExpectedState wire format for existing BCC verification.

        This projection is NOT a dispatch/admission contract. It cannot enforce
        effect IDs, DAG, max age, privacy or budgets. Consumers must retain the
        original IR and bind its digest before dispatch; existing finalization
        must independently observe post-state. Runtime wiring is disabled/not
        installed by this module. Never infer a grant from this method.
        """
        return [{"kind": v["kind"], "target": v["target"], "expect": v["expect"]}
                for effect in self.to_dict()["effects"] for v in effect["verifiers"]]

    def effect_bindings(self) -> list[dict[str, Any]]:
        """Stable per-obligation bindings; execution attempt is added downstream."""
        raw = self.to_dict()
        return [{"mission_id": raw["mission_id"], "mission_digest": self.digest,
                 "revision": raw["revision"], "effect_id": effect["effect_id"],
                 "verifier_index": index, "expectation_digest": hashlib.sha256(
                     _canonical(v).encode("utf-8")).hexdigest(),
                 "max_age_seconds": v["max_age_seconds"]}
                for effect in raw["effects"] for index, v in enumerate(effect["verifiers"])]


def _json_types(raw: Any) -> None:
    stack = [(raw, 0)]
    seen = 0
    while stack:
        value, depth = stack.pop()
        seen += 1
        if depth > 32 or seen > 100_000:
            _fail("JSON nesting or node limit exceeded")
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                _fail("JSON object keys must be strings")
            for key in value:
                _utf8_size(key)
            stack.extend((v, depth + 1) for v in value.values())
        elif type(value) is list:
            stack.extend((v, depth + 1) for v in value)
        elif type(value) is str:
            _utf8_size(value)
        elif type(value) not in (int, float, bool, type(None)):
            _fail("non-JSON value")
