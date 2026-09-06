from reference_implementation.admission import AdmissionKernel
from reference_implementation.models import Condition, Lifecycle, ObjectiveRuntimeState, Proposal
from reference_implementation.store import InMemoryReferenceStore
from reference_implementation.system_optimizer import CandidateImprovement, PromotionEvidence, may_promote

def state(lifecycle=Lifecycle.ACTIVE):
    return ObjectiveRuntimeState(
        objective_id="obj-1", owner_id="owner", scope_id="scope",
        spec_digest="digest", revision=1, lifecycle=lifecycle, condition=Condition.DEVIATED
    )

def proposal():
    return Proposal(
        proposal_id="p", owner_id="owner", scope_id="scope", objective_id="obj-1",
        objective_digest="digest", objective_revision=1, observation_ids=("o",),
        observation_digests=("od",), requested_capabilities=("file.edit",),
        expected_effects=({"kind":"file","path":"fixture.txt"},), created_at=1, valid_until=10
    )

def kernel(store):
    return AdmissionKernel(
        store,
        policy_check=lambda p:(True,"ok"),
        reserve_budget=lambda p:(True,"ok"),
        conflict_check=lambda p:(True,"ok"),
    )

def test_revoked_never_admits():
    s=InMemoryReferenceStore(); s.put_new_objective(state(Lifecycle.REVOKED))
    assert not kernel(s).admit(proposal(), now=2, current_spec_digest="digest", current_revision=1).admitted

def test_stale_revision_never_admits():
    s=InMemoryReferenceStore(); s.put_new_objective(state())
    d=kernel(s).admit(proposal(), now=2, current_spec_digest="digest", current_revision=2)
    assert not d.admitted and "revision" in d.reason

def test_duplicate_reservation_denied():
    s=InMemoryReferenceStore(); s.put_new_objective(state())
    k=kernel(s)
    assert k.admit(proposal(), now=2, current_spec_digest="digest", current_revision=1).admitted
    assert not k.admit(proposal(), now=2, current_spec_digest="digest", current_revision=1).admitted

def test_optimizer_blocks_trust_kernel():
    ok,reason=may_promote(
        CandidateImprovement("policy_kernel","1","2","faster"),
        PromotionEvidence(.8,.9,1.0,True,True,100)
    )
    assert not ok and "trust-critical" in reason

def test_optimizer_blocks_intelligence_regression():
    ok,reason=may_promote(
        CandidateImprovement("context_policy","1","2","less noise"),
        PromotionEvidence(.8,.9,.97,True,True,100)
    )
    assert not ok and "intelligence" in reason
