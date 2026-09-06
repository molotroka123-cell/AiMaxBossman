class NotAdmitted(RuntimeError): pass

def to_mission_ir_candidate(proposal, decision, *, verification_requirements, privacy, risk):
    """Reference payload only; port into canonical MissionIR constructor."""
    if not decision.admitted or not decision.mission_intent_id:
        raise NotAdmitted("proposal has no current admission")
    return {
        "mission_intent_id": decision.mission_intent_id,
        "objective": {
            "objective_id": proposal.objective_id,
            "objective_digest": proposal.objective_digest,
            "objective_revision": proposal.objective_revision,
        },
        "required_effects": list(proposal.expected_effects),
        "verification_requirements": list(verification_requirements),
        "required_capabilities": list(proposal.requested_capabilities),
        "privacy": privacy,
        "risk": risk,
        "reservation_id": decision.reservation_id,
    }
