from __future__ import annotations
from hashlib import sha256
import json
from .models import AdmissionDecision, Lifecycle, Proposal

def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class AdmissionKernel:
    """Reference check order only; production must use canonical services atomically."""
    def __init__(self, store, *, policy_check, reserve_budget, conflict_check):
        self.store = store
        self.policy_check = policy_check
        self.reserve_budget = reserve_budget
        self.conflict_check = conflict_check

    def admit(self, proposal: Proposal, *, now: float, current_spec_digest: str, current_revision: int):
        state = self.store.get_objective(proposal.objective_id)
        if state.lifecycle is not Lifecycle.ACTIVE:
            return AdmissionDecision(False, f"lifecycle={state.lifecycle.value}")
        if state.stopped:
            return AdmissionDecision(False, "stop condition active")
        if state.spec_digest != current_spec_digest or state.spec_digest != proposal.objective_digest:
            return AdmissionDecision(False, "stale objective digest")
        if state.revision != current_revision or state.revision != proposal.objective_revision:
            return AdmissionDecision(False, "stale objective revision")
        if now >= proposal.valid_until:
            return AdmissionDecision(False, "proposal expired")
        ok, reason = self.conflict_check(proposal)
        if not ok: return AdmissionDecision(False, "conflict:" + reason)
        ok, reason = self.policy_check(proposal)
        if not ok: return AdmissionDecision(False, "policy:" + reason)
        ok, reason = self.reserve_budget(proposal)
        if not ok: return AdmissionDecision(False, "budget:" + reason)
        reservation_id = _digest({"proposal_id": proposal.proposal_id, "objective_digest": proposal.objective_digest, "revision": proposal.objective_revision})
        if not self.store.reserve_once(reservation_id):
            return AdmissionDecision(False, "duplicate reservation")
        mission_intent_id = _digest({"reservation_id": reservation_id, "effects": proposal.expected_effects, "capabilities": proposal.requested_capabilities})
        return AdmissionDecision(True, "admitted", reservation_id, mission_intent_id)
