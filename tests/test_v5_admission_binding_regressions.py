"""Adversarial admission inputs must never reuse a recorded grant for new work."""
from copy import deepcopy
from dataclasses import replace

import pytest

from bossman_shared.objective_admission import reauthorize_at_effect_boundary
from bossman_shared.objective_mission import MissionAdaptationError, to_mission_ir
from test_v5_admission import (
    NOW, OWNER, SCOPE, OBJECTIVE, EFFECT, CAPABILITY,
    FakePolicy, FakeTreasury, FakeConflicts, ready, propose, kernel,
)


@pytest.mark.parametrize('field,value', [
    ('valid_until', NOW + 999999),
    ('freshness_deadline', NOW + 999999),
    ('trigger', 'owner_request'),
    ('requested_capabilities', ('network.egress',)),
    ('proposal_id', 'unrecorded-proposal'),
])
def test_changed_proposal_is_refused_before_external_ports(tmp_path, field, value):
    store, spec, _ = ready(tmp_path)
    proposal = replace(propose(store, spec), **{field: value})
    treasury = FakeTreasury()
    policy = FakePolicy({'perm:repo.write'}, {CAPABILITY, 'network.egress'})
    decision = kernel(policy=policy, treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert not decision.admitted
    assert treasury.reserved == []


def test_mutated_effect_cannot_be_admitted(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    effect = deepcopy(proposal.expected_effects[0])
    effect['verifiers'][0]['target'] = '/other-owner/private.json'
    changed = replace(proposal, expected_effects=(effect,))
    treasury = FakeTreasury()
    assert not kernel(treasury=treasury).admit(store, changed, now=NOW + 1).admitted
    assert treasury.reserved == []


@pytest.mark.parametrize('now', [float('nan'), float('inf'), -float('inf'), True, NOW - 1])
def test_invalid_or_backwards_admission_time_fails_closed(tmp_path, now):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    treasury = FakeTreasury()
    assert not kernel(treasury=treasury).admit(store, proposal, now=now).admitted
    assert treasury.reserved == []


def test_replayed_admission_does_not_release_original_conflict_claim(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    treasury, conflicts = FakeTreasury(), FakeConflicts()
    engine = kernel(treasury=treasury, conflicts=conflicts)
    assert engine.admit(store, proposal, now=NOW + 1).admitted
    assert not engine.admit(store, proposal, now=NOW + 2).admitted
    assert conflicts.held == {'repo:main': OBJECTIVE}
    assert len(treasury.reserved) == 1
    assert treasury.released == []


def test_mission_translation_refuses_post_admission_mutation(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    effect = deepcopy(proposal.expected_effects[0])
    effect['verifiers'][0]['target'] = '/wrong-target'
    changed = replace(proposal, expected_effects=(effect,))
    with pytest.raises(MissionAdaptationError):
        to_mission_ir(changed, decision, owner_id=OWNER, project_id=SCOPE, goal='test')


@pytest.mark.parametrize('owner,scope', [('foreign-owner', SCOPE), (OWNER, 'foreign-scope')])
def test_mission_translation_cannot_reassign_owner_or_scope(tmp_path, owner, scope):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    with pytest.raises(MissionAdaptationError):
        to_mission_ir(proposal, decision, owner_id=owner, project_id=scope, goal='test')


def test_translation_cannot_expand_reserved_budget(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    with pytest.raises(MissionAdaptationError):
        to_mission_ir(proposal, decision, owner_id=OWNER, project_id=SCOPE, goal='test',
                      budget={'max_cost_usd': 1000.0, 'max_tokens': 1000, 'max_wall_seconds': 30.0})


def test_effect_boundary_requires_live_policy_and_denies_revoked_grants(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    policy = FakePolicy({'perm:repo.write'}, {CAPABILITY})
    engine = kernel(policy=policy)
    decision = engine.admit(store, proposal, now=NOW + 1)
    assert decision.admitted
    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 2)
    assert reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 2, policy=policy)
    policy.permissions.clear()
    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 2, policy=policy)


@pytest.mark.parametrize('field,value', [
    ('mission_intent_id', 'other-mission'), ('proposal_id', 'other-proposal'),
    ('objective_id', 'other-objective'), ('owner_id', 'other-owner'),
    ('scope_id', 'other-scope'), ('decided_at', NOW + .5),
    ('authorized_scope_refs', ('scope:foreign',)),
])
def test_substituted_decision_cannot_use_an_open_reservation(tmp_path, field, value):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    engine = kernel()
    decision = engine.admit(store, proposal, now=NOW + 1)
    assert decision.admitted
    forged = replace(decision, **{field: value})
    assert not reauthorize_at_effect_boundary(store, forged, proposal, now=NOW + 2, policy=engine.policy)


@pytest.mark.parametrize('now', [float('nan'), float('inf'), True, NOW])
def test_effect_boundary_refuses_invalid_or_backwards_clock(tmp_path, now):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    engine = kernel()
    decision = engine.admit(store, proposal, now=NOW + 1)
    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=now, policy=engine.policy)


def test_revoke_during_treasury_call_cannot_publish_ready_authority(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)

    class RevokingTreasury(FakeTreasury):
        def reserve(self, scopes, estimate):
            result = super().reserve(scopes, estimate)
            store.transition(OBJECTIVE, 'REVOKED', now=NOW + 1, owner_id=OWNER,
                             expected_version=state.version)
            return result

    treasury, conflicts = RevokingTreasury(), FakeConflicts()
    decision = kernel(treasury=treasury, conflicts=conflicts).admit(store, proposal, now=NOW + 1)
    assert not decision.admitted
    assert len(treasury.reserved) == len(treasury.released) == 1
    assert not conflicts.held
    assert not store.open_reservations(OBJECTIVE)


def test_exception_after_external_hold_stays_pending_across_restart(tmp_path):
    from bossman_shared.objective_store import ObjectiveStore
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)

    class LostAckTreasury(FakeTreasury):
        def reserve(self, scopes, estimate):
            super().reserve(scopes, estimate)
            raise RuntimeError('acknowledgement lost after reserve')

    treasury = LostAckTreasury()
    decision = kernel(treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert not decision.admitted
    restarted = ObjectiveStore(store.path)
    held = restarted.open_reservations(OBJECTIVE)
    assert len(held) == 1 and held[0]['payload']['phase'] == 'PENDING'
    assert not kernel(treasury=treasury).admit(restarted, proposal, now=NOW + 2).admitted
    assert len(treasury.reserved) == 1


def test_independent_store_connections_serialize_duplicate_admission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from bossman_shared.objective_store import ObjectiveStore
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    treasury, conflicts = FakeTreasury(), FakeConflicts()
    engine = kernel(treasury=treasury, conflicts=conflicts)
    barrier = Barrier(8)

    def attempt(_):
        independent = ObjectiveStore(store.path)
        barrier.wait(timeout=10)
        return engine.admit(independent, proposal, now=NOW + 1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(attempt, range(8)))
    assert sum(d.admitted for d in decisions) == 1
    assert len(treasury.reserved) == 1 and treasury.released == []
    assert conflicts.held == {'repo:main': OBJECTIVE}
    assert len(store.open_reservations(OBJECTIVE)) == 1


def test_cumulative_quota_rechecked_after_proposal(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)
    store.record_mission_usage(OBJECTIVE, missions=5, wall_seconds=0.0, cost_usd=0.0,
                               expected_version=state.version)
    treasury = FakeTreasury()
    decision = kernel(treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert not decision.admitted and treasury.reserved == []


def test_lower_mission_budget_remains_valid(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    mission = to_mission_ir(proposal, decision, owner_id=OWNER, project_id=SCOPE, goal='test',
                            budget={'max_cost_usd': .25})
    assert mission.to_dict()['budget']['max_cost_usd'] == .25


def test_policy_exception_releases_only_owned_claim(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)

    class UnavailablePolicy(FakePolicy):
        def check_grants(self, *args):
            raise RuntimeError('policy unavailable')

    conflicts, treasury = FakeConflicts(), FakeTreasury()
    decision = kernel(policy=UnavailablePolicy(set(), set()), conflicts=conflicts,
                       treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert not decision.admitted and not conflicts.held
    assert treasury.reserved == [] and store.open_reservations(OBJECTIVE) == []


@pytest.mark.parametrize('field,value', [('valid_until', None), ('freshness_deadline', 'forever')])
def test_malformed_proposal_horizon_is_a_refusal_not_an_exception(tmp_path, field, value):
    store, spec, _ = ready(tmp_path)
    proposal = replace(propose(store, spec), **{field: value})
    assert not kernel().admit(store, proposal, now=NOW + 1).admitted


def test_mutation_during_port_callback_is_not_published(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)

    class MutatingTreasury(FakeTreasury):
        def reserve(self, scopes, estimate):
            result = super().reserve(scopes, estimate)
            proposal.expected_effects[0]['verifiers'][0]['target'] = '/substituted'
            return result

    treasury = MutatingTreasury()
    decision = kernel(treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert not decision.admitted
    assert len(treasury.reserved) == len(treasury.released) == 1
    assert not store.open_reservations(OBJECTIVE)


def test_second_proposal_cannot_reenter_same_objective_conflict_hold(tmp_path):
    store, spec, _ = ready(tmp_path)
    first = propose(store, spec, observation_id='first')
    second = propose(store, spec, observation_id='second')
    treasury, conflicts = FakeTreasury(), FakeConflicts()
    engine = kernel(treasury=treasury, conflicts=conflicts)
    assert engine.admit(store, first, now=NOW + 1).admitted
    assert not engine.admit(store, second, now=NOW + 2).admitted
    assert len(treasury.reserved) == 1
    assert conflicts.held == {'repo:main': OBJECTIVE}


def test_concurrent_settlement_is_final_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from bossman_shared.objective_store import ObjectiveStore, ObjectiveStoreError
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    barrier = Barrier(8)

    def settle(i):
        independent = ObjectiveStore(store.path)
        barrier.wait(timeout=10)
        try:
            independent.settle_reservation(decision.reservation_id, 'COMMITTED' if i % 2 else 'RELEASED')
            return True
        except ObjectiveStoreError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(settle, range(8))) == 1
    assert len([e for e in store.journal(OBJECTIVE) if e['event'] == 'settled']) == 1
