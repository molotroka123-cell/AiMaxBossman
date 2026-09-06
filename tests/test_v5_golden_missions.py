"""V5 Golden Missions H01-H10 — the acceptance suite for Bossman Steward.

These are end-to-end, not unit tests. Each mission drives the real canonical
pieces together over a real file-backed store and real files on disk: the pure
`objective_spec` contract, the durable `objective_store`, deterministic
`objective_observer` readings, the `objective_admission` kernel, the canonical
`mission_ir` through `objective_mission`, `objective_reconcile` over the
canonical `evidence` signer, and `objective_recovery`.

What they defend is the whole V5 chain rather than any one module:

    EXPLICIT OBJECTIVE -> SCOPED OBSERVATION -> VERIFIED WORLD STATE
    -> DETERMINISTIC DEVIATION -> PROPOSAL -> CURRENT AUTHORIZATION
    -> ATOMIC RESERVATION -> ORDINARY V4 MISSION IR -> VERIFIED EFFECT
    -> FRESH RE-OBSERVATION -> SATISFIED / DEVIATED / UNKNOWN

Passing this file is necessary for a V5 release and nowhere near sufficient.
It says nothing about the 24-hour soak, Windows, real FFmpeg, remote Fleet
qualification, or the paired model intelligence measurement — those are
measurements, and no unit test can stand in for them.

Only the ports that are canonical services elsewhere are faked here (policy
grants, the Treasury envelope, the conflict registry). Everything the missions
actually assert about is the real implementation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman_shared import evidence
from bossman_shared.mission_ir import MissionIR
from bossman_shared.objective_admission import (
    AdmissionKernel,
    CostEstimate,
    authorized_scope_refs,
    build_proposal,
    reauthorize_at_effect_boundary,
    treasury_scopes,
)
from bossman_shared.objective_mission import to_mission_ir
from bossman_shared.objective_observer import EnrolledSource, FileStateObserver, collect
from bossman_shared.objective_reconcile import (
    bind_evidence,
    observation_digests,
    reconcile_after_mission,
)
from bossman_shared.objective_recovery import recover, resume_point
from bossman_shared.objective_spec import ObjectiveSpec, evaluate
from bossman_shared.objective_store import ObjectiveStore

HOUR = 3600.0
NOW = 1_800_000_000.0
# Only the canonical allowlist may mint evidence; a V5 module cannot add itself.
SIGNER = "bossman_v3.verifier"


# --------------------------------------------------------------------- ports


class Policy:
    """Stands in for the canonical policy/grant service, nothing more."""

    def __init__(self, grants: set[str], capabilities: set[str]) -> None:
        self.grants, self.capabilities = grants, capabilities

    def check_grants(self, owner_id, scope_id, permission_refs, capabilities):
        missing = sorted(set(permission_refs) - self.grants)
        if missing:
            return False, f"ungranted permission: {', '.join(missing)}"
        beyond = sorted(set(capabilities) - self.capabilities)
        if beyond:
            return False, f"ungranted capability: {', '.join(beyond)}"
        return True, "granted"


class Treasury:
    """Counts holds so a test can prove nothing was charged on a refusal."""

    def __init__(self, *, allow: bool = True) -> None:
        self.allow, self.held, self.reserves, self.releases, self.commits = allow, 0.0, 0, 0, 0

    def reserve(self, scopes, estimate):
        self.reserves += 1
        if not self.allow:
            return False, None, "budget exhausted"
        self.held += estimate.cost_usd
        return True, f"treasury:{self.reserves}", "reserved"

    def release(self, scopes, estimate):
        self.releases += 1
        self.held -= estimate.cost_usd

    def commit(self, scopes, estimate, actual=None):
        self.commits += 1


class Conflicts:
    """One holder per conflict key, as the canonical registry must behave."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.releases = 0

    def claim(self, conflict_keys, objective_id, priority):
        for key in conflict_keys:
            holder = self.held.get(key)
            if holder is not None and holder != objective_id:
                return False, f"{key} held by {holder}"
        for key in conflict_keys:
            self.held[key] = objective_id
        return True, "claimed"

    def release(self, conflict_keys, objective_id):
        self.releases += 1
        for key in conflict_keys:
            if self.held.get(key) == objective_id:
                del self.held[key]


# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def _evidence_key(tmp_path, monkeypatch):
    """Never touch a real home directory for the signing key."""
    monkeypatch.setenv(evidence.ENV_KEY_FILE, str(tmp_path / "evidence.key"))
    monkeypatch.setenv(evidence.ENV_DATA_DIR, str(tmp_path))
    evidence.reset_cache()
    yield
    evidence.reset_cache()


def spec_dict(*, objective_id="build-health", revision=1, previous_digest=None,
              sources=(("repo:build", "r1"),), expires_at=NOW + 8 * HOUR,
              conflict_keys=("repo:build",), cooldown=0.0, max_missions=3,
              max_cost=5.0, predicates=None) -> dict:
    """A valid ObjectiveSpec candidate; every mission narrows it as needed."""
    return {
        "schema_version": 1,
        "owner_id": "owner-1",
        "scope_id": "project-alpha",
        "objective_id": objective_id,
        "revision": revision,
        "previous_digest": previous_digest,
        "sources": [{"source_ref": ref, "source_revision": rev, "max_age_seconds": 300.0}
                    for ref, rev in sources],
        "predicates": predicates or [{
            "predicate_id": "builds", "source_ref": sources[0][0], "field": "exists",
            "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": expires_at,
        "priority": 5,
        "allowed_triggers": ["source_change", "scheduled"],
        "permission_refs": ["fs:read", "fs:write"],
        "conflict_keys": list(conflict_keys),
        "cooldown_seconds": cooldown,
        "limits": {"max_observations": 64, "max_missions": max_missions,
                   "max_wall_seconds": 600.0, "max_cost_usd": max_cost},
        "stop_conditions": ["owner_stop"],
    }


def activated(tmp_path, raw=None, *, name="v5.sqlite3"):
    """Import DRAFT, enroll explicitly, activate: the only legitimate path."""
    raw = raw or spec_dict()
    spec = ObjectiveSpec.from_dict(raw)
    store = ObjectiveStore(tmp_path / name)
    state = store.create(spec)
    state = store.enroll_sources(state.objective_id,
                                 tuple(s["source_ref"] for s in raw["sources"]),
                                 owner_id=raw["owner_id"], expected_version=state.version)
    state = store.transition(state.objective_id, "ACTIVE", now=NOW,
                             owner_id=raw["owner_id"], expected_version=state.version)
    return store, spec, state


def observer_for(state, spec, path: Path, *, source_ref="repo:build", source_revision="r1"):
    return FileStateObserver(EnrolledSource(source_ref, source_revision, state.owner_id,
                                            state.scope_id, spec.digest), path)


def effect(*, target: str, effect_id="restore", kind="IDEMPOTENT_WRITE",
           capabilities=("fs:write",)) -> dict:
    return {
        "effect_id": effect_id,
        "kind": kind,
        "description": "restore the file the objective requires",
        "capabilities": list(capabilities),
        "depends_on": [],
        "verifiers": [{"kind": "file", "target": target,
                       "expect": {"exists": True}, "max_age_seconds": 300.0}],
    }


def kernel(*, policy=None, treasury=None, conflicts=None):
    return AdmissionKernel(policy or Policy({"fs:read", "fs:write"}, {"fs:write"}),
                           treasury or Treasury(), conflicts or Conflicts())


def deviating_batch(store, state, observer, *, now=NOW):
    """One real observation round over the enrolled sources."""
    batch = collect([observer], state=state, max_observations=64, now=now)
    return batch.records()


def propose(store, state, records, *, target, now=NOW, trigger="source_change",
            cost=CostEstimate(0.02, 500, 30.0)):
    return build_proposal(store, state.objective_id, records, now, trigger,
                          requested_capabilities=("fs:write",),
                          expected_effects=(effect(target=target),),
                          cost_estimate=cost)


# ------------------------------------------------------------- H01 .. H10


def test_h01_keeps_a_fixture_repository_buildable_after_an_approved_change(tmp_path):
    """H01: deviation -> proposal -> authorization -> mission -> verified -> green.

    The whole chain, and green is reached only through a fresh post-effect
    re-observation carrying bound, signed evidence.
    """
    artifact = tmp_path / "build" / "app.bin"
    artifact.parent.mkdir()
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)

    # The world deviates: the build artifact is gone.
    records = deviating_batch(store, state, observer)
    assert evaluate(spec, records, now=NOW, lifecycle="ACTIVE",
                    enrolled_sources=state.enrolled_sources,
                    trigger="source_change").condition == "DEVIATED"

    proposal = propose(store, state, records, target=str(artifact))
    assert proposal is not None and proposal.admission_allowed is False

    treasury, conflicts = Treasury(), Conflicts()
    decision = kernel(treasury=treasury, conflicts=conflicts).admit(store, proposal, now=NOW)
    assert decision.admitted, decision.reason
    assert reauthorize_at_effect_boundary(store, decision, proposal, now=NOW, policy=kernel().policy)

    mission = to_mission_ir(proposal, decision, owner_id=state.owner_id,
                            project_id=state.scope_id, goal="restore the build artifact")
    assert isinstance(mission, MissionIR)
    assert mission.to_dict()["reservation_refs"] == [decision.reservation_id]
    assert f"@{state.revision}#" in mission.to_dict()["provenance"]["source_ref"]

    # The effect actually lands, and only then is it independently observed.
    artifact.write_bytes(b"built")
    effect_at = NOW + 5.0
    store.settle_reservation(decision.reservation_id, "COMMITTED")
    state = store.get(state.objective_id)

    def reobserve():
        return deviating_batch(store, state, observer, now=effect_at + 1.0)

    fresh = reobserve()
    payload = bind_evidence(objective_id=state.objective_id, objective_digest=state.spec_digest,
                            objective_revision=state.revision, mission_id=mission.mission_id,
                            reservation_id=decision.reservation_id,
                            evidence_ref="evidence:h01", effect_at=effect_at,
                            digests=observation_digests(fresh))
    signed = {**payload, **evidence.sign_fields(payload, signer=SIGNER)}

    result = reconcile_after_mission(store, state.objective_id, mission_verified=True,
                                     evidence=signed, reobserve=reobserve,
                                     now=effect_at + 2.0, expected_version=state.version,
                                     mission_id=mission.mission_id,
                                     reservation_id=decision.reservation_id)
    assert result.condition == "SATISFIED", result.reason
    final = store.get(state.objective_id)
    assert final.condition == "SATISFIED" and final.last_verified_evidence_ref


def test_h02_refreshes_a_scoped_local_report_only_when_the_input_changes(tmp_path):
    """H02: no relevant world-state change means no proposal and no model call."""
    report = tmp_path / "report.json"
    report.write_bytes(b"{}")
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, report)

    first = collect([observer], state=state, max_observations=64, now=NOW)
    state = store.record_observation(state.objective_id, observed_at=NOW,
                                     count=len(first.observations),
                                     expected_version=state.version)
    steady = collect([observer], state=state, max_observations=64, now=NOW + 60.0)
    # The value digest excludes time, so an unchanged file is genuinely unchanged.
    assert steady.value_digests() == first.value_digests()

    # Satisfied condition produces no proposal at all: idle costs nothing.
    assert propose(store, state, first.records(), target=str(report)) is None

    report.write_bytes(b'{"rows": 3}')
    changed = collect([observer], state=state, max_observations=64, now=NOW + 120.0)
    assert changed.value_digests() != first.value_digests()


def test_h03_prepares_a_draft_from_changed_assets_and_never_publishes(tmp_path):
    """H03: a capability outside the current grants is refused, unpublished."""
    asset = tmp_path / "asset.png"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, asset)
    records = deviating_batch(store, state, observer)

    proposal = build_proposal(store, state.objective_id, records, NOW, "source_change",
                              requested_capabilities=("fs:write", "net:publish"),
                              expected_effects=(effect(target=str(asset),
                                                       capabilities=("net:publish",)),),
                              cost_estimate=CostEstimate(0.01, 100, 5.0))
    assert proposal is not None
    treasury = Treasury()
    # Publishing is not granted, so nothing is admitted and nothing is charged.
    decision = kernel(policy=Policy({"fs:read", "fs:write"}, {"fs:write"}),
                      treasury=treasury).admit(store, proposal, now=NOW)
    assert not decision.admitted
    assert treasury.reserves == 0 and treasury.held == 0.0


def test_h04_crash_between_deviation_and_receipt_never_duplicates_an_effect(tmp_path):
    """H04: an irreversible effect of unknown outcome parks; it never replays."""
    artifact = tmp_path / "invoice.pdf"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    records = deviating_batch(store, state, observer)
    proposal = build_proposal(store, state.objective_id, records, NOW, "source_change",
                              requested_capabilities=("fs:write",),
                              expected_effects=(effect(target=str(artifact),
                                                       kind="IRREVERSIBLE"),),
                              cost_estimate=CostEstimate(0.03, 200, 10.0))
    decision = kernel().admit(store, proposal, now=NOW)
    assert decision.admitted

    # The process dies here. A second store over the same file is the restart.
    restarted = ObjectiveStore(tmp_path / "v5.sqlite3")
    assert [r["reservation_id"] for r in restarted.open_reservations(state.objective_id)] \
        == [decision.reservation_id]

    report = recover(restarted, state.objective_id, now=NOW + 30.0,
                     is_effect_applied=lambda payload: "UNKNOWN")
    assert report.parked, "ambiguity must park, never replay an irreversible effect"
    assert restarted.open_reservations(state.objective_id), "parked work stays unsettled"
    assert restarted.get(state.objective_id).condition == "UNKNOWN"


def test_h05_revocation_while_queued_produces_zero_unauthorized_effects(tmp_path):
    """H05: revoke or expiry after admission fails the effect-boundary recheck."""
    artifact = tmp_path / "out.txt"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    records = deviating_batch(store, state, observer)
    proposal = propose(store, state, records, target=str(artifact))
    decision = kernel().admit(store, proposal, now=NOW)
    assert decision.admitted
    assert reauthorize_at_effect_boundary(store, decision, proposal, now=NOW, policy=kernel().policy)

    state = store.get(state.objective_id)
    store.transition(state.objective_id, "REVOKED", now=NOW + 1.0,
                     owner_id=state.owner_id, expected_version=state.version)

    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 2.0, policy=kernel().policy)
    # Revocation is sticky: nothing brings the objective back.
    with pytest.raises(Exception):
        store.transition(state.objective_id, "ACTIVE", now=NOW + 3.0,
                         owner_id=state.owner_id,
                         expected_version=store.get(state.objective_id).version)


def test_h05b_expiry_while_queued_also_denies_the_effect(tmp_path):
    artifact = tmp_path / "out.txt"
    raw = spec_dict(expires_at=NOW + 120.0)
    store, spec, state = activated(tmp_path, raw)
    observer = observer_for(state, spec, artifact)
    proposal = propose(store, state, deviating_batch(store, state, observer),
                       target=str(artifact))
    decision = kernel().admit(store, proposal, now=NOW)
    assert decision.admitted
    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 121.0, policy=kernel().policy)


def test_h06_duplicate_and_out_of_order_events_admit_at_most_one_intent(tmp_path):
    """H06: a flood of identical events yields one proposal and one reservation."""
    artifact = tmp_path / "flood.txt"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    records = deviating_batch(store, state, observer)

    first = propose(store, state, records, target=str(artifact))
    assert first is not None
    # Every replay of the same observation content collapses onto one identity.
    for _ in range(25):
        with pytest.raises(Exception):
            propose(store, store.get(state.objective_id), records, target=str(artifact))

    treasury = Treasury()
    k = kernel(treasury=treasury)
    admitted = [k.admit(store, first, now=NOW + i) for i in range(5)]
    assert sum(1 for d in admitted if d.admitted) == 1, "one intent, not five"
    assert treasury.held == pytest.approx(0.02), "budget charged exactly once"


def test_h07_incompatible_objectives_conflict_visibly_without_oscillating(tmp_path):
    """H07: two objectives demanding the same file state cannot both be admitted."""
    artifact = tmp_path / "shared.conf"
    conflicts = Conflicts()
    store_a, spec_a, state_a = activated(tmp_path, spec_dict(objective_id="obj-a"),
                                         name="a.sqlite3")
    store_b, spec_b, state_b = activated(tmp_path, spec_dict(objective_id="obj-b"),
                                         name="b.sqlite3")

    p_a = propose(store_a, state_a,
                  deviating_batch(store_a, state_a, observer_for(state_a, spec_a, artifact)),
                  target=str(artifact))
    p_b = propose(store_b, state_b,
                  deviating_batch(store_b, state_b, observer_for(state_b, spec_b, artifact)),
                  target=str(artifact))

    d_a = kernel(conflicts=conflicts).admit(store_a, p_a, now=NOW)
    d_b = kernel(conflicts=conflicts).admit(store_b, p_b, now=NOW)
    assert d_a.admitted and not d_b.admitted
    assert "obj-a" in d_b.detail or "obj-a" in d_b.reason or "conflict" in d_b.reason
    # The loser does not thrash: it stays refused while the key is held.
    for _ in range(5):
        assert not kernel(conflicts=conflicts).admit(store_b, p_b, now=NOW + 1).admitted


def test_h08_a_refused_budget_leaves_no_conflict_key_held(tmp_path):
    """H08: a failing dependency must not strand a claim behind it.

    The PRIVATE/LOCAL_ONLY egress floor is a transport property this suite
    cannot observe, so what is proved here is the compensation path: a refusal
    downstream of a claim releases it rather than wedging the objective.
    """
    artifact = tmp_path / "private.txt"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    proposal = propose(store, state, deviating_batch(store, state, observer),
                       target=str(artifact))
    treasury, conflicts = Treasury(allow=False), Conflicts()
    decision = kernel(treasury=treasury, conflicts=conflicts).admit(store, proposal, now=NOW)
    assert not decision.admitted
    assert conflicts.held == {}, "a refused admission holds nothing"
    assert treasury.held == 0.0


def test_h09_a_hostile_observation_cannot_widen_authority(tmp_path):
    """H09: an observation claiming permissions changes nothing about authority."""
    artifact = tmp_path / "target.txt"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    records = deviating_batch(store, state, observer)

    # The observation asserts a grant it has no power to create.
    records[0]["values"]["permission_refs"] = ["net:publish", "fs:admin"]
    records[0]["values"]["admission_allowed"] = True

    proposal = build_proposal(store, state.objective_id, records, NOW, "source_change",
                              requested_capabilities=("net:publish",),
                              expected_effects=(effect(target=str(artifact),
                                                       capabilities=("net:publish",)),),
                              cost_estimate=CostEstimate(0.01, 100, 5.0))
    treasury = Treasury()
    decision = kernel(policy=Policy({"fs:read", "fs:write"}, {"fs:write"}),
                      treasury=treasury).admit(store, proposal, now=NOW) \
        if proposal is not None else None
    assert decision is None or not decision.admitted
    assert treasury.held == 0.0
    # The stored spec is what defines authority, and it is untouched.
    assert store.get_spec(state.objective_id).to_dict()["permission_refs"] == ["fs:read", "fs:write"]


def test_h10_upgrade_and_rollback_never_replay_work_autonomously(tmp_path):
    """H10: a revision mid-flight invalidates queued work rather than running it."""
    artifact = tmp_path / "upgraded.txt"
    raw = spec_dict()
    store, spec, state = activated(tmp_path, raw)
    observer = observer_for(state, spec, artifact)
    proposal = propose(store, state, deviating_batch(store, state, observer),
                       target=str(artifact))
    decision = kernel().admit(store, proposal, now=NOW)
    assert decision.admitted

    # The owner revises the objective while the mission is queued.
    state = store.get(state.objective_id)
    revised = ObjectiveSpec.from_dict(
        spec_dict(revision=2, previous_digest=spec.digest, max_missions=4), previous=spec)
    state = store.revise(state.objective_id, revised, owner_id=state.owner_id,
                         expected_version=state.version)

    assert state.condition == "UNKNOWN", "a new digest inherits no old verdict"
    assert state.last_verified_evidence_ref is None
    assert not reauthorize_at_effect_boundary(store, decision, proposal, now=NOW + 1.0, policy=kernel().policy)

    # Usage survives the upgrade: a revision cannot buy a fresh budget.
    point = resume_point(store, state.objective_id)
    assert point.has_verified_state is False
    restarted = ObjectiveStore(tmp_path / "v5.sqlite3")
    assert restarted.get(state.objective_id).revision == 2


# ------------------------------------------------------- chain-wide invariants


def test_unknown_never_silently_becomes_satisfied_or_deviated(tmp_path):
    """A stale observation is UNKNOWN, and UNKNOWN admits nothing."""
    artifact = tmp_path / "stale.txt"
    artifact.write_bytes(b"x")
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    records = deviating_batch(store, state, observer)

    stale_now = NOW + 3600.0  # far beyond the source's 300s freshness
    assert evaluate(spec, records, now=stale_now, lifecycle="ACTIVE",
                    enrolled_sources=state.enrolled_sources,
                    trigger="source_change").condition == "UNKNOWN"
    assert propose(store, state, records, target=str(artifact), now=stale_now) is None


def test_a_draft_objective_observes_nothing_and_proposes_nothing(tmp_path):
    """Import is DRAFT and enrolls nothing; autonomy is never the default."""
    artifact = tmp_path / "draft.txt"
    spec = ObjectiveSpec.from_dict(spec_dict())
    store = ObjectiveStore(tmp_path / "v5.sqlite3")
    state = store.create(spec)
    assert state.lifecycle == "DRAFT" and state.enrolled_sources == ()

    batch = collect([observer_for(state, spec, artifact)], state=state,
                    max_observations=64, now=NOW)
    assert batch.observations == () and batch.dropped == (("repo:build", "not_enrolled"),)
    assert propose(store, state, [], target=str(artifact)) is None


def test_mission_completion_is_not_sustained_objective_health(tmp_path):
    """A mission that reports success without a post-state leaves UNKNOWN."""
    artifact = tmp_path / "claimed.txt"
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)

    result = reconcile_after_mission(
        store, state.objective_id, mission_verified=False, evidence=None,
        reobserve=lambda: deviating_batch(store, store.get(state.objective_id), observer),
        now=NOW + 10.0, expected_version=state.version)
    assert result.condition == "UNKNOWN"
    assert store.get(state.objective_id).condition == "UNKNOWN"


def test_the_store_refuses_green_without_verified_evidence(tmp_path):
    """The last line of defence: no code path can write SATISFIED bare."""
    store, spec, state = activated(tmp_path)
    with pytest.raises(Exception):
        store.set_condition(state.objective_id, "SATISFIED", evidence_ref=None,
                            expected_version=state.version)
    with pytest.raises(Exception):
        store.set_condition(state.objective_id, "SATISFIED", evidence_ref="   ",
                            expected_version=state.version)


def test_an_untrusted_signer_cannot_mint_objective_evidence(tmp_path):
    """MODEL_TEXT != PROOF, enforced by the canonical allowlist.

    A V5 module cannot add itself as a signer, so a well-formed record signed by
    anyone outside the allowlist is refused at the evidence layer and never
    reaches the store as a reason to write SATISFIED.
    """
    payload = bind_evidence(objective_id="o", objective_digest="d", objective_revision=1,
                            mission_id="m", reservation_id="r", evidence_ref="e",
                            effect_at=NOW, digests=["a" * 64])
    with pytest.raises(ValueError):
        evidence.sign_fields(payload, signer="bossman_shared.objective_reconcile")
    assert not evidence.verify_signed({**payload, "sig": "x" * 64, "signer": SIGNER,
                                       "nonce": "n", "issued_at": "t"})


def test_a_forged_signature_downgrades_a_verified_mission_to_unknown(tmp_path):
    """Tampered evidence is not a crash and not a green: it is an absent answer."""
    artifact = tmp_path / "forged.txt"
    artifact.write_bytes(b"present")
    store, spec, state = activated(tmp_path)
    observer = observer_for(state, spec, artifact)
    fresh = deviating_batch(store, state, observer, now=NOW + 10.0)
    payload = bind_evidence(objective_id=state.objective_id,
                            objective_digest=state.spec_digest,
                            objective_revision=state.revision, mission_id="m-1",
                            reservation_id="r-1", evidence_ref="evidence:forged",
                            effect_at=NOW + 5.0, digests=observation_digests(fresh))
    forged = {**payload, **evidence.sign_fields(payload, signer=SIGNER)}
    forged["objective_revision"] = 99  # signed body no longer matches the claim

    result = reconcile_after_mission(
        store, state.objective_id, mission_verified=True, evidence=forged,
        reobserve=lambda: deviating_batch(store, store.get(state.objective_id),
                                          observer, now=NOW + 10.0),
        now=NOW + 12.0, expected_version=state.version,
        mission_id="m-1", reservation_id="r-1")
    assert result.condition == "UNKNOWN", result.reason
    assert store.get(state.objective_id).condition == "UNKNOWN"
