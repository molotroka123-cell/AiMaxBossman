"""Post-mission reconciliation: mission completion is not objective health.

Two conflations this module exists to refuse:

* MISSION_COMPLETION != SUSTAINED_OBJECTIVE_HEALTH. A finished mission says the
  agent stopped working; it says nothing about the world. Health is written only
  from a *fresh* observation batch taken after the effect and evaluated by
  `bossman_shared.objective_spec.evaluate`.
* TOOL_SUCCESS != VERIFIED_EFFECT. An executor return code is a claim by the
  actor. Unless the post-state was independently observed, the condition becomes
  UNKNOWN — never SATISFIED, never DEVIATED.

Deliberate non-goals. No observer, no mission dispatcher, no scheduler and no
second evidence signer live here: `bossman_shared.evidence` is the only signer,
and observation is injected as a callable so this module can never fabricate the
world it reports on. Nothing here dispatches, retries or approves anything.

Invariants:

* UNKNOWN dominates. Any failed gate collapses the outcome to UNKNOWN, and no
  gate can ever upgrade an UNKNOWN evaluation (`apply_unknown_dominance`).
* SATISFIED requires an evidence record that verifies under
  `evidence.verify_signed` *and* binds objective id, spec digest, revision,
  mission id, reservation id and the digests of the re-observed batch. Evidence
  that binds nothing proves nothing, so unbound evidence is unverified evidence.
* An observation older than the effect cannot testify about the post-effect
  world: it is refused as proof and the condition stays UNKNOWN.
* A downgrade never erases history. The stored `last_verified_evidence_ref` is
  carried forward on UNKNOWN so a long-horizon resume still knows the last thing
  that was actually verified.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from typing import Any

from . import evidence as _evidence
from .mission_ir import MissionIRValidationError, _canonical
from .objective_spec import ObjectiveValidationError, evaluate
from .objective_store import CONDITIONS, ObjectiveStore

MAX_BATCH = 128

REASON_MISSION_UNVERIFIED = "mission_effect_not_independently_verified"
REASON_REOBSERVE_FAILED = "reobservation_unavailable"
REASON_EVIDENCE_UNSIGNED = "evidence_signature_invalid"
REASON_EVIDENCE_BINDING = "evidence_binding_mismatch"
REASON_PRE_EFFECT = "observation_precedes_effect"
REASON_EVALUATION_UNKNOWN = "evaluation_unknown"
REASON_UNSUPPORTED_CONDITION = "unsupported_condition"
REASON_FRESH = "fresh_verified_reobservation"

# Gate name -> machine-readable reason. Ordering of the gates at the call site is
# what makes a failure explainable; the first failed gate names the outcome.
_GATE_REASONS = {
    "mission_verified": REASON_MISSION_UNVERIFIED,
    "reobserved": REASON_REOBSERVE_FAILED,
    "evidence_verified": REASON_EVIDENCE_UNSIGNED,
    "evidence_bound": REASON_EVIDENCE_BINDING,
    "observed_after_effect": REASON_PRE_EFFECT,
}

BINDING_FIELDS = ("objective_id", "objective_digest", "objective_revision", "mission_id",
                  "reservation_id", "evidence_ref", "effect_at", "observation_digests")


class ReconcileError(RuntimeError):
    """The caller asked for something structurally impossible, not merely unproven."""


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """What this reconciliation proved. Never an authorization to do more work."""

    objective_id: str
    condition: str
    evidence_refs: tuple[str, ...]
    reason: str
    used_observations: bool
    observation_digests: tuple[str, ...]
    version: int


def apply_unknown_dominance(evaluated: str, gates: Sequence[tuple[str, bool]]) -> tuple[str, str]:
    """Collapse to UNKNOWN on the first failed gate; never upgrade an UNKNOWN.

    Split out of `reconcile_after_mission` so the rule is testable at the
    boundary rather than buried in the middle of an I/O path: a gate may only
    ever *lower* an outcome, so passing every gate still leaves an UNKNOWN
    evaluation UNKNOWN.
    """
    for name, ok in gates:
        if not ok:
            return "UNKNOWN", _GATE_REASONS.get(name, f"gate_failed:{name}")
    if evaluated not in CONDITIONS:
        return "UNKNOWN", REASON_UNSUPPORTED_CONDITION
    if evaluated == "UNKNOWN":
        return "UNKNOWN", REASON_EVALUATION_UNKNOWN
    return evaluated, REASON_FRESH


def observation_digests(batch: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Content digests of an observation batch, order-independent.

    Same canonicalisation as `objective_spec.project_proposal`, so evidence and
    admission agree on what "these observations" means byte for byte.
    """
    try:
        return tuple(sorted(
            hashlib.sha256(_canonical(dict(o)).encode("utf-8")).hexdigest() for o in batch))
    except (MissionIRValidationError, TypeError, ValueError) as exc:
        raise ReconcileError("observation batch is not canonicalisable") from exc


def bind_evidence(*, objective_id: str, objective_digest: str, objective_revision: int,
                  mission_id: str, reservation_id: str, evidence_ref: str, effect_at: float,
                  digests: Sequence[str]) -> dict[str, Any]:
    """The payload a verifier must sign for its evidence to count here.

    Every field is a binding: without them a signature proves only that *some*
    trusted signer said *something*, which is not proof about this objective,
    this mission or these observations.
    """
    return {
        "objective_id": objective_id,
        "objective_digest": objective_digest,
        "objective_revision": int(objective_revision),
        "mission_id": mission_id,
        "reservation_id": reservation_id,
        "evidence_ref": evidence_ref,
        "effect_at": float(effect_at),
        "observation_digests": list(digests),
    }


def _nonempty(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and type(value) is not bool and math.isfinite(value)


def _bindings_ok(record: Mapping[str, Any], *, objective_id: str, objective_digest: str,
                 objective_revision: int, mission_id: str | None, reservation_id: str | None,
                 digests: tuple[str, ...]) -> bool:
    if any(field not in record for field in BINDING_FIELDS):
        return False
    if record["objective_id"] != objective_id or record["objective_digest"] != objective_digest:
        return False
    if record["objective_revision"] != objective_revision:
        return False
    for field, expected in (("mission_id", mission_id), ("reservation_id", reservation_id)):
        if not _nonempty(record[field]):
            return False
        # A caller that knows the identity pins it; one that does not still
        # requires the evidence to name *some* mission and reservation.
        if expected is not None and record[field] != expected:
            return False
    if not _nonempty(record["evidence_ref"]) or not _finite(record["effect_at"]):
        return False
    bound = record["observation_digests"]
    return type(bound) is list and tuple(bound) == digests


def reconcile_after_mission(store: ObjectiveStore, objective_id: str, *, mission_verified: bool,
                            evidence: Mapping[str, Any] | None,
                            reobserve: Callable[[], list[dict[str, Any]]], now: float,
                            expected_version: int, mission_id: str | None = None,
                            reservation_id: str | None = None,
                            evidence_key: bytes | None = None) -> ReconcileResult:
    """Set objective health from a fresh post-effect observation, or UNKNOWN.

    `reobserve` is injected: this module never reads the world itself, so a
    mission cannot reconcile itself green by reporting its own success. The
    returned batch is charged against the observation quota even when it fails
    a later gate — the observation really did happen.
    """
    if type(mission_verified) is not bool:
        raise ReconcileError("mission_verified must be an independently observed boolean")
    if not callable(reobserve):
        raise ReconcileError("a fresh re-observation callable is required")
    state = store.get(objective_id)
    version = expected_version
    carried = state.last_verified_evidence_ref

    def _write(condition: str, reason: str, refs: tuple[str, ...], used: bool,
               digests: tuple[str, ...], at_version: int) -> ReconcileResult:
        # UNKNOWN keeps the previously verified reference: a downgrade means "we
        # no longer know", not "nothing was ever verified".
        ref = refs[0] if refs else carried
        written = store.set_condition(objective_id, condition, evidence_ref=ref,
                                      expected_version=at_version)
        return ReconcileResult(objective_id, condition, refs, reason, used, digests,
                               written.version)

    if not mission_verified:
        # No independent post-state observation exists at all: re-observing now
        # would still not tell us the mission's effects landed.
        condition, reason = apply_unknown_dominance("UNKNOWN", (("mission_verified", False),))
        return _write(condition, reason, (), False, (), version)

    try:
        batch = reobserve()
        if type(batch) is not list or not batch or len(batch) > MAX_BATCH:
            batch = None
        elif any(type(o) is not dict for o in batch):
            batch = None
    except Exception:  # an observer that fails is ignorance, never health
        batch = None
    if batch is None:
        condition, reason = apply_unknown_dominance(
            "UNKNOWN", (("mission_verified", True), ("reobserved", False)))
        return _write(condition, reason, (), False, (), version)

    digests = observation_digests(batch)
    observed_at = [o.get("observed_at") for o in batch]
    latest = max((t for t in observed_at if _finite(t)), default=now)
    charged = store.record_observation(objective_id, observed_at=latest, count=len(batch),
                                       expected_version=version)
    version = charged.version

    verified = bool(evidence is not None and isinstance(evidence, Mapping)
                    and _evidence.verify_signed(evidence, key=evidence_key))
    bound = verified and _bindings_ok(
        evidence, objective_id=objective_id, objective_digest=state.spec_digest,
        objective_revision=state.revision, mission_id=mission_id,
        reservation_id=reservation_id, digests=digests)
    # Only trusted-and-bound evidence may supply the effect time; otherwise the
    # freshness test would be answered by the party being audited.
    effect_at = float(evidence["effect_at"]) if bound else None
    fresh = bool(effect_at is not None
                 and all(_finite(t) and t >= effect_at for t in observed_at))

    evaluated = "UNKNOWN"
    if fresh:
        try:
            evaluated = evaluate(store.get_spec(objective_id), batch, now=now,
                                 lifecycle=state.lifecycle,
                                 enrolled_sources=state.enrolled_sources).condition
        except ObjectiveValidationError:
            evaluated = "UNKNOWN"
    condition, reason = apply_unknown_dominance(evaluated, (
        ("mission_verified", True), ("reobserved", True), ("evidence_verified", verified),
        ("evidence_bound", bound), ("observed_after_effect", fresh)))
    refs = (str(evidence["evidence_ref"]),) if bound and condition != "UNKNOWN" else ()
    return _write(condition, reason, refs, True, digests, version)
