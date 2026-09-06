"""Admission kernel tests: a proposal is never an authorization.

Every port is a small in-test fake so the assertions are about ORDER and
COMPENSATION, not about the canonical policy/Treasury/conflict implementations.
The store is a real file-backed `ObjectiveStore` (never `:memory:`), because the
once-only fences under test are database constraints, not Python checks.
"""
from __future__ import annotations

import math

import pytest

from bossman_shared.mission_ir import MissionIR
from bossman_shared.objective_admission import (
    ADMITTED,
    BUDGET_DENIED,
    CAPABILITY_NOT_GRANTED,
    CONFLICT_HELD,
    COOLDOWN_ACTIVE,
    COST_ESTIMATE_UNKNOWN,
    DUPLICATE_RESERVATION,
    LIFECYCLE_NOT_ACTIVE,
    OBJECTIVE_EXPIRED,
    PROPOSAL_EXPIRED,
    STALE_REVISION,
    AdmissionKernel,
    AlreadyProposed,
    CostEstimate,
    build_proposal,
    reauthorize_at_effect_boundary,
)
from bossman_shared.objective_mission import NotAdmitted, to_mission_ir
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore

NOW = 1_000_000.0
OWNER = "owner:ada"
SCOPE = "scope:workshop"
OBJECTIVE = "objective:keep-build-green"
CAPABILITY = "files.write"


# ------------------------------------------------------------------- fixtures


def spec_dict(*, revision: int = 1, previous_digest: str | None = None,
              expires_at: float = NOW + 100_000.0, max_age: float = 3600.0,
              cooldown: float = 0.0) -> dict:
    return {
        "schema_version": 1,
        "owner_id": OWNER,
        "scope_id": SCOPE,
        "objective_id": OBJECTIVE,
        "revision": revision,
        "previous_digest": previous_digest,
        "predicates": [{"predicate_id": "p1", "source_ref": "src:build", "field": "green",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "sources": [{"source_ref": "src:build", "source_revision": "r1",
                     "max_age_seconds": max_age}],
        "expires_at": expires_at,
        "priority": 5,
        "allowed_triggers": ["scheduled"],
        "permission_refs": ["perm:repo.write"],
        "conflict_keys": ["repo:main"],
        "cooldown_seconds": cooldown,
        "limits": {"max_observations": 100, "max_missions": 5,
                   "max_wall_seconds": 1000.0, "max_cost_usd": 10.0},
        "stop_conditions": ["owner_stop"],
    }


def observation(digest: str, *, observed_at: float = NOW - 10.0,
                observation_id: str = "obs-1") -> dict:
    return {
        "observation_id": observation_id,
        "owner_id": OWNER,
        "scope_id": SCOPE,
        "objective_digest": digest,
        "source_ref": "src:build",
        "source_revision": "r1",
        "observed_at": observed_at,
        "values": {"green": False},
    }


EFFECT = {
    "effect_id": "e1",
    "kind": "IDEMPOTENT_WRITE",
    "description": "rewrite the failing lockfile",
    "capabilities": [CAPABILITY],
    "depends_on": [],
    "verifiers": [{"kind": "file", "target": "/repo/lock.json",
                   "expect": {"exists": True, "min_bytes": 2}, "max_age_seconds": 300}],
}


class FakePolicy:
    """Dict-backed current grants; a capability outside them is not authority."""

    def __init__(self, permissions: set[str], capabilities: set[str]) -> None:
        self.permissions = permissions
        self.capabilities = capabilities
        self.calls = 0

    def check_grants(self, owner_id, scope_id, permission_refs, capabilities):
        self.calls += 1
        if not set(permission_refs) <= self.permissions:
            return False, "permission_not_granted"
        if not set(capabilities) <= self.capabilities:
            return False, "capability_not_granted"
        return True, "granted"


class FakeTreasury:
    """Counting adapter onto the canonical reserve/commit/release semantics."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.reserved: list[tuple] = []
        self.released: list[tuple] = []
        self.committed: list[tuple] = []

    def reserve(self, scopes, estimate):
        self.reserved.append((scopes, estimate))
        if not self.allow:
            return False, "", "budget exceeded in organization"
        return True, f"treasury:{len(self.reserved)}", "within all envelopes"

    def release(self, scopes, estimate):
        self.released.append((scopes, estimate))

    def commit(self, scopes, estimate, actual):
        self.committed.append((scopes, estimate, actual))


class FakeConflicts:
    """Set-backed exclusive registry of conflict keys to their holder."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.releases: list[tuple[tuple[str, ...], str]] = []

    def claim(self, conflict_keys, objective_id, priority):
        blocked = [k for k in conflict_keys if self.held.get(k, objective_id) != objective_id]
        if blocked:
            return False, f"held by {self.held[blocked[0]]}"
        for key in conflict_keys:
            self.held[key] = objective_id
        return True, "claimed"

    def release(self, conflict_keys, objective_id):
        self.releases.append((tuple(conflict_keys), objective_id))
        for key in conflict_keys:
            if self.held.get(key) == objective_id:
                del self.held[key]


def make_store(tmp_path, **spec_kwargs):
    store = ObjectiveStore(tmp_path / "objectives.sqlite3")
    spec = ObjectiveSpec.from_dict(spec_dict(**spec_kwargs))
    state = store.create(spec)
    return store, spec, state


def activate(store, state):
    state = store.enroll_sources(OBJECTIVE, ("src:build",), owner_id=OWNER,
                                 expected_version=state.version)
    return store.transition(OBJECTIVE, "ACTIVE", now=NOW, owner_id=OWNER,
                            expected_version=state.version)


def propose(store, spec, *, now=NOW, estimate=CostEstimate(0.5, 1000, 30.0),
            capabilities=(CAPABILITY,), observed_at=NOW - 10.0, observation_id="obs-1"):
    return build_proposal(
        store, OBJECTIVE,
        [observation(spec.digest, observed_at=observed_at, observation_id=observation_id)],
        now, "scheduled", requested_capabilities=tuple(capabilities),
        expected_effects=(EFFECT,), cost_estimate=estimate)


def kernel(*, policy=None, treasury=None, conflicts=None):
    return AdmissionKernel(policy or FakePolicy({"perm:repo.write"}, {CAPABILITY}),
                           treasury or FakeTreasury(),
                           conflicts or FakeConflicts())


def ready(tmp_path, **spec_kwargs):
    store, spec, state = make_store(tmp_path, **spec_kwargs)
    state = activate(store, state)
    return store, spec, state


# ---------------------------------------------------------------------- tests


def test_draft_objective_produces_no_proposal(tmp_path):
    store, spec, _ = make_store(tmp_path)
    assert propose(store, spec) is None


def test_paused_objective_is_never_admitted(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)
    assert proposal is not None
    store.transition(OBJECTIVE, "PAUSED", now=NOW, owner_id=OWNER,
                     expected_version=state.version)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    assert (decision.admitted, decision.reason) == (False, LIFECYCLE_NOT_ACTIVE)


def test_revoke_before_admission_is_refused(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)
    store.transition(OBJECTIVE, "REVOKED", now=NOW, owner_id=OWNER,
                     expected_version=state.version)
    treasury = FakeTreasury()
    decision = kernel(treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert decision.reason == LIFECYCLE_NOT_ACTIVE
    assert treasury.reserved == []


def test_revoke_while_queued_fails_effect_boundary_reauthorization(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    assert decision.admitted
    assert reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 2)
    store.transition(OBJECTIVE, "REVOKED", now=NOW, owner_id=OWNER,
                     expected_version=state.version)
    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 3)


def test_objective_expiry_between_proposal_and_admission_is_refused(tmp_path):
    store, spec, _ = ready(tmp_path, expires_at=NOW + 50.0)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 60.0)
    assert (decision.admitted, decision.reason) == (False, OBJECTIVE_EXPIRED)


def test_revision_between_proposal_and_admission_is_stale(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)
    revised = ObjectiveSpec.from_dict(
        spec_dict(revision=2, previous_digest=spec.digest), previous=spec)
    store.revise(OBJECTIVE, revised, owner_id=OWNER, expected_version=state.version)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    assert (decision.admitted, decision.reason) == (False, STALE_REVISION)


def test_expired_proposal_is_refused(tmp_path):
    store, spec, _ = ready(tmp_path, max_age=60.0)
    proposal = propose(store, spec)
    assert proposal.valid_until == NOW + 50.0
    decision = kernel().admit(store, proposal, now=NOW + 100.0)
    assert (decision.admitted, decision.reason) == (False, PROPOSAL_EXPIRED)


def test_duplicate_proposal_identity_yields_one_admitted_intent(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    with pytest.raises(AlreadyProposed):
        propose(store, spec)
    treasury, conflicts = FakeTreasury(), FakeConflicts()
    engine = kernel(treasury=treasury, conflicts=conflicts)
    first = engine.admit(store, proposal, now=NOW + 1)
    second = engine.admit(store, proposal, now=NOW + 2)
    assert first.admitted and not second.admitted
    assert second.reason == DUPLICATE_RESERVATION
    # The replay took a speculative hold; the compensation path gave it back.
    assert len(treasury.reserved) == 2 and len(treasury.released) == 1
    assert conflicts.releases == [(("repo:main",), OBJECTIVE)]
    assert len(store.open_reservations(OBJECTIVE)) == 1


def test_ungranted_capability_is_refused_without_charging_treasury(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec, capabilities=(CAPABILITY, "network.egress"))
    treasury, conflicts = FakeTreasury(), FakeConflicts()
    decision = kernel(treasury=treasury, conflicts=conflicts).admit(store, proposal, now=NOW + 1)
    assert (decision.admitted, decision.reason) == (False, CAPABILITY_NOT_GRANTED)
    assert treasury.reserved == []
    assert conflicts.releases == [(("repo:main",), OBJECTIVE)] and conflicts.held == {}


def test_treasury_refusal_releases_the_conflict_claim(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    conflicts = FakeConflicts()
    decision = kernel(treasury=FakeTreasury(allow=False),
                      conflicts=conflicts).admit(store, proposal, now=NOW + 1)
    assert (decision.admitted, decision.reason) == (False, BUDGET_DENIED)
    assert conflicts.releases == [(("repo:main",), OBJECTIVE)] and conflicts.held == {}
    assert store.open_reservations(OBJECTIVE) == []


def test_conflict_key_held_by_another_objective_is_refused(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    conflicts = FakeConflicts()
    conflicts.held["repo:main"] = "objective:other"
    treasury = FakeTreasury()
    decision = kernel(treasury=treasury, conflicts=conflicts).admit(store, proposal, now=NOW + 1)
    assert (decision.admitted, decision.reason) == (False, CONFLICT_HELD)
    assert treasury.reserved == []


@pytest.mark.parametrize("estimate", [None, CostEstimate(math.nan, 10, 1.0),
                                      CostEstimate(math.inf, 10, 1.0)])
def test_unknown_cost_is_never_reserved_as_zero(tmp_path, estimate):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec, estimate=estimate)
    treasury = FakeTreasury()
    decision = kernel(treasury=treasury).admit(store, proposal, now=NOW + 1)
    assert (decision.admitted, decision.reason) == (False, COST_ESTIMATE_UNKNOWN)
    assert treasury.reserved == []


def test_cooldown_is_rechecked_against_a_newer_proposal_at_admission(tmp_path):
    store, spec, _ = ready(tmp_path, cooldown=600.0)
    proposal = propose(store, spec)
    # A newer proposal lands while this one waits: the cooldown window moved,
    # and a proposal never carries authority through it.
    store.insert_proposal_once(proposal_id="newer", objective_id=OBJECTIVE,
                               objective_digest=spec.digest, objective_revision=1,
                               created_at=NOW + 5.0, valid_until=NOW + 500.0, payload={})
    treasury = FakeTreasury()
    decision = kernel(treasury=treasury).admit(store, proposal, now=NOW + 6.0)
    assert (decision.admitted, decision.reason) == (False, COOLDOWN_ACTIVE)
    assert treasury.reserved == []


def test_own_proposal_timestamp_is_not_a_cooldown_against_itself(tmp_path):
    store, spec, _ = ready(tmp_path, cooldown=600.0)
    proposal = propose(store, spec)
    assert kernel().admit(store, proposal, now=NOW + 1).reason == ADMITTED


def test_admitted_proposal_becomes_a_valid_traceable_mission_ir(tmp_path):
    store, spec, state = ready(tmp_path)
    proposal = propose(store, spec)
    decision = kernel().admit(store, proposal, now=NOW + 1)
    assert decision.admitted and decision.reservation_id
    mission = to_mission_ir(proposal, decision, owner_id=OWNER, project_id=SCOPE,
                            goal="restore the green build")
    assert isinstance(mission, MissionIR)
    raw = mission.to_dict()
    assert raw["reservation_refs"] == [decision.reservation_id]
    assert raw["provenance"] == {
        "source": "v5_objective",
        "source_ref": f"{OBJECTIVE}@{state.revision}#{proposal.proposal_id}"}
    assert f"scope:{SCOPE}" in raw["authorized_scope_refs"][0]
    # Digest stability: revalidating the exported contract reproduces it exactly.
    assert MissionIR.from_dict(raw).digest == mission.digest
    assert MissionIR.from_json(mission.to_json()).digest == mission.digest
    assert all(effect["verifiers"] for effect in raw["effects"])


def test_to_mission_ir_refuses_a_non_admitted_decision(tmp_path):
    store, spec, _ = ready(tmp_path)
    proposal = propose(store, spec)
    refused = kernel(treasury=FakeTreasury(allow=False)).admit(store, proposal, now=NOW + 1)
    assert not refused.admitted
    with pytest.raises(NotAdmitted):
        to_mission_ir(proposal, refused, owner_id=OWNER, project_id=SCOPE, goal="anything")
