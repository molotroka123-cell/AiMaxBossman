"""Translate an admitted V5 proposal into the CANONICAL Epoch 4 Mission IR.

This module only translates. It creates no second mission contract, no
dispatcher, no finalizer and no evidence signer: `bossman_shared.mission_ir` is
the one mission contract, and every mission built here is a real `MissionIR`
produced through `MissionIR.from_dict`, so its validation -- not a copy of it --
is what a translated mission must survive.

Invariants carried:

* Nothing is built without a current admission. `decision.admitted` and a
  reservation id are preconditions, so a translated mission always corresponds
  to money and conflict keys actually held.
* Provenance binds the mission to the exact objective revision and proposal that
  authorized it: `<objective_id>@<revision>#<proposal_id>`. The reservation id
  travels in `reservation_refs` and the granted scopes in
  `authorized_scope_refs`, so the mission is traceable in both directions.
* Every declared effect must carry independent post-state verifiers. Mission IR
  already enforces that; this module refuses early with a clear error rather
  than fabricating a verifier to get past validation.
* An effect may not require a capability the admission did not grant. Widening
  scope during translation would make the grant check meaningless.

Deliberate non-goals. No dispatch, no reconciliation, no health writing, no
budget enforcement (the Treasury owns that) and no re-authorization -- the
effect boundary re-check lives in `bossman_shared.objective_admission`.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .mission_ir import MissionIR
from .objective_admission import AdmissionDecision, AdmissionProposal, decision_matches_proposal

DEFAULT_RECOVERY = {"max_attempts_per_effect": 1, "max_attempts_total": 3}


class MissionAdaptationError(ValueError):
    """The admitted proposal cannot be translated into a mission contract."""


class NotAdmitted(MissionAdaptationError):
    """No current admission: there is nothing this mission would be doing under."""


class MissingPostStateVerifier(MissionAdaptationError):
    """A declared effect has no independent post-state obligation."""


class UngrantedCapability(MissionAdaptationError):
    """An effect asks for a capability the admission did not grant."""


def to_mission_ir(proposal: AdmissionProposal, decision: AdmissionDecision, *,
                  owner_id: str, project_id: str, goal: str,
                  privacy: str = "local_only", risk: str = "low",
                  success_conditions: Iterable[str] = (),
                  budget: Mapping[str, Any] | None = None,
                  recovery: Mapping[str, Any] | None = None,
                  artifact_refs: Iterable[str] = ()) -> MissionIR:
    """Build the canonical Mission IR for one admitted proposal."""
    if type(proposal) is not AdmissionProposal or type(decision) is not AdmissionDecision:
        raise MissionAdaptationError("recorded proposal and decision required")
    if not decision.admitted or not decision.reservation_id or not decision.mission_intent_id:
        raise NotAdmitted("proposal has no current admission")
    if not decision_matches_proposal(decision, proposal):
        raise MissionAdaptationError("decision does not bind this exact proposal content")
    if owner_id != decision.owner_id or project_id != decision.scope_id:
        raise MissionAdaptationError("mission owner/scope differs from admission")
    reserved_budget = _budget(proposal)
    if budget is not None:
        if set(budget) - set(reserved_budget):
            raise MissionAdaptationError("unknown budget dimensions")
        for key, value in budget.items():
            if (type(value) not in (int, float) or not 0 <= value <= reserved_budget[key]
                    or (key == "max_tokens" and type(value) is not int)):
                raise MissionAdaptationError("mission budget exceeds reservation or is malformed")
        reserved_budget.update(budget)

    granted = set(decision.granted_capabilities)
    effects = []
    for raw in proposal.expected_effects:
        if type(raw) is not dict:
            raise MissionAdaptationError("effect: JSON object required")
        effect = dict(raw)
        verifiers = effect.get("verifiers")
        # Surfaced here rather than left to mission_ir so the operator learns
        # that the PROPOSAL is underspecified, not that the mission is malformed.
        if type(verifiers) is not list or not verifiers:
            raise MissingPostStateVerifier(
                f"effect {effect.get('effect_id')!r} declares no post-state verifier")
        missing = sorted(set(effect.get("capabilities") or ()) - granted)
        if missing:
            raise UngrantedCapability(
                f"effect {effect.get('effect_id')!r} requires ungranted: {', '.join(missing)}")
        effects.append(effect)
    if not effects:
        raise MissionAdaptationError("an admitted proposal must declare its effects")

    side_effect = any(e.get("kind") != "READ_ONLY" for e in effects)
    conditions = list(success_conditions) or [
        f"objective {decision.objective_id}@{decision.objective_revision} "
        "re-observed after verified effects"]
    raw_mission = {
        "schema_version": 1,
        "owner_id": owner_id,
        "project_id": project_id,
        "mission_id": decision.mission_intent_id,
        "goal_id": decision.objective_id,
        "revision": 1,
        "previous_digest": None,
        "goal": goal,
        "side_effect": side_effect,
        "effects": effects,
        "privacy": privacy,
        "risk": risk,
        "authorized_scope_refs": list(decision.authorized_scope_refs),
        "budget": reserved_budget,
        "reservation_refs": [decision.reservation_id],
        "recovery": dict(recovery) if recovery is not None else dict(DEFAULT_RECOVERY),
        "success_conditions": conditions,
        "artifact_refs": list(artifact_refs),
        "provenance": {
            "source": "v5_objective",
            "source_ref": (f"{decision.objective_id}@{decision.objective_revision}"
                           f"#{proposal.proposal_id}"),
        },
    }
    return MissionIR.from_dict(raw_mission)


def _budget(proposal: AdmissionProposal) -> dict[str, Any]:
    """The reserved estimate is the mission's ceiling; unknown cost never ships."""
    estimate = proposal.cost_estimate
    if estimate is None or not estimate.is_known():
        raise MissionAdaptationError("admitted proposal carries no bounded cost estimate")
    return {"max_cost_usd": float(estimate.cost_usd), "max_tokens": int(estimate.tokens),
            "max_wall_seconds": float(estimate.wall_seconds)}
