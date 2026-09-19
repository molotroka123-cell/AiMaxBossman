"""Reality Compiler: owner intent -> a validated Mission IR *candidate*.

The compiler's whole job is to turn a sentence into a structure that can be
checked. It grants nothing. That has to be stated as a rule rather than left
implicit, because the failure mode is subtle and attractive: a compiler that
also decided what a mission may do would become a second authorization path,
reachable by anyone who can phrase a request — and it would be the easiest one,
so everything would drift towards it.

So, explicitly:

  * `MissionIR != authorization`. The IR names declared effects; whether any of
    them may happen is still decided by the permission model, the approval
    scope and the evidence gates, at the moment of the effect.
  * Compilation never widens scope. Everything the IR asserts must be present
    in the intent the owner actually gave; the compiler does not infer an
    effect nobody asked for, and it does not invent an obligation from prose.
    That inference is precisely what manufactured `file:example.com` and
    deadlocked two tasks in the acceptance corpus.
  * A refusal is a real answer. An intent the compiler cannot express as a
    checkable contract comes back as INCOMPLETE with what is missing, not as a
    best-effort IR that later reads as a promise nobody made.

The validated structure itself is `bossman_shared.mission_ir.MissionIR` — the
existing digest-bound, append-only contract. Defining a second one here would
be the dual-authority mistake this module exists to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bossman_shared.mission_ir import MissionIR, MissionIRValidationError

OK = "ok"
INCOMPLETE = "incomplete"
REFUSED = "refused"

#: Effect kinds, from least to most dangerous, as the shared contract defines
#: them. `side_effect` on the mission is a BOOLEAN — "does this mutate anything"
#: — and the compiler derives it from the declared effects rather than trusting
#: a caller who might mark a mutating mission informational.
_EFFECT_ORDER = ("READ_ONLY", "IDEMPOTENT_WRITE", "REVERSIBLE_WRITE", "IRREVERSIBLE")

#: The shared contract requires exact key sets for these objects. Defaults are
#: deliberately small: an intent that does not state a budget gets a modest one,
#: never an unbounded one, because "unspecified" must not read as "unlimited".
_DEFAULT_BUDGET = {"max_cost_usd": 1.0, "max_tokens": 100_000, "max_wall_seconds": 900}
_DEFAULT_RECOVERY = {"max_attempts_per_effect": 2, "max_attempts_total": 6}
_DEFAULT_PROVENANCE = {"source": "reality_compiler", "source_ref": "owner_intent"}


@dataclass
class CompileResult:
    status: str
    mission: MissionIR | None = None
    missing: list[str] = field(default_factory=list)
    reason: str = ""
    #: What the compiler declined to infer, and why. Visible on purpose: an
    #: owner should be able to see that their prose was NOT turned into an
    #: obligation, rather than discovering it when a review fails.
    not_inferred: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == OK and self.mission is not None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "missing": list(self.missing),
                "reason": self.reason, "not_inferred": list(self.not_inferred),
                "digest": self.mission.digest if self.mission else None,
                "mission": self.mission.to_dict() if self.mission else None}


def _require(value: Any, name: str, missing: list[str]) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        missing.append(name)
        return None
    return value


def _merged(defaults: dict[str, Any], given: Any) -> dict[str, Any]:
    """Fill the contract's required keys without letting a caller add new ones —
    the shared validator rejects an unexpected key, and silently dropping it
    would hide that the caller asked for something unsupported."""
    out = dict(defaults)
    if isinstance(given, dict):
        for key, value in given.items():
            if key in defaults:
                out[key] = value
            else:
                out[key] = value          # kept, so the validator can refuse it loudly
    return out


def compile_intent(intent: dict[str, Any]) -> CompileResult:
    """Compile a structured owner intent into a Mission IR candidate.

    `intent` is what the owner (or a UI on their behalf) actually stated. It is
    NOT free prose: every field the IR asserts must be supplied, because the
    alternative — deriving obligations from a sentence — is the defect that
    produced a `file:example.com` obligation nobody promised.
    """
    if not isinstance(intent, dict):
        return CompileResult(REFUSED, reason="намерение должно быть объектом")

    missing: list[str] = []
    goal = _require(intent.get("goal"), "goal", missing)
    owner_id = _require(intent.get("owner_id"), "owner_id", missing)
    project_id = _require(intent.get("project_id"), "project_id", missing)
    mission_id = _require(intent.get("mission_id"), "mission_id", missing)
    goal_id = _require(intent.get("goal_id"), "goal_id", missing)

    declared = intent.get("effects")
    not_inferred: list[str] = []
    if declared is None:
        # Silence about effects is not "no effects" and not "any effect": it is
        # an incomplete intent. Guessing either way is how a mission acquires an
        # obligation, or an authority, that nobody stated.
        missing.append("effects")
        declared = []
        if isinstance(intent.get("goal"), str):
            not_inferred.append("обязательства НЕ выведены из текста цели — "
                                "объявьте effects явно")
    elif not isinstance(declared, list):
        return CompileResult(REFUSED, reason="effects должен быть списком")

    if missing:
        return CompileResult(INCOMPLETE, missing=missing, not_inferred=not_inferred,
                             reason="намерение не содержит полей, без которых контракт "
                                    "нельзя проверить")

    # `side_effect` is derived, not taken on trust. A mission that declares a
    # mutating effect IS effectful, whatever its summary says; the reverse
    # direction (claiming effectful with only reads) is the caller's to make and
    # is harmless, since it only tightens what downstream gates demand.
    mutating = _mutating(declared)
    stated = intent.get("side_effect")
    if isinstance(stated, bool) and stated is False and mutating:
        return CompileResult(
            REFUSED,
            reason=("намерение объявляет side_effect=false при изменяющих эффектах "
                    f"({_strongest_declared(declared)}); компилятор не смягчает "
                    "класс эффекта"))
    side_effect = bool(stated) if isinstance(stated, bool) else mutating
    if mutating:
        side_effect = True

    raw = {
        "schema_version": 1,
        "owner_id": owner_id, "project_id": project_id,
        "mission_id": mission_id, "goal_id": goal_id,
        "revision": int(intent.get("revision") or 1),
        "previous_digest": intent.get("previous_digest"),
        "goal": goal,
        "side_effect": side_effect,
        "effects": declared,
        "privacy": str(intent.get("privacy") or "public"),
        "risk": str(intent.get("risk") or "low"),
        # Scope and reservation references are UNTRUSTED identifiers that
        # downstream policy resolves. Passing them through is not granting them.
        "authorized_scope_refs": list(intent.get("authorized_scope_refs") or []),
        "budget": _merged(_DEFAULT_BUDGET, intent.get("budget")),
        "reservation_refs": list(intent.get("reservation_refs") or []),
        "recovery": _merged(_DEFAULT_RECOVERY, intent.get("recovery")),
        "success_conditions": list(intent.get("success_conditions") or []),
        "artifact_refs": list(intent.get("artifact_refs") or []),
        "provenance": _merged(_DEFAULT_PROVENANCE, intent.get("provenance")),
    }
    try:
        # `from_dict`, never direct construction: the shared contract makes
        # `MissionIR(...)` raise on purpose, so the only way in is through
        # validation.
        mission = MissionIR.from_dict(raw)
    except MissionIRValidationError as exc:
        # The shared contract's refusal is reported verbatim: re-wording it here
        # would create a second, softer account of why something was rejected.
        return CompileResult(REFUSED, reason=str(exc), not_inferred=not_inferred)
    return CompileResult(OK, mission=mission, not_inferred=not_inferred)


def _strongest_declared(effects: list[Any]) -> str:
    strongest = "READ_ONLY"
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        kind = str(effect.get("kind") or "READ_ONLY")
        if kind in _EFFECT_ORDER and _EFFECT_ORDER.index(kind) > _EFFECT_ORDER.index(strongest):
            strongest = kind
    return strongest


def _mutating(effects: list[Any]) -> bool:
    return _strongest_declared(effects) != "READ_ONLY"
