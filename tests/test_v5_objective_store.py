"""Hostile tests for the durable V5 objective store; persistence is the claim."""
import json
import sqlite3
import threading

import pytest

from bossman_shared.objective_spec import (
    ObjectiveSpec,
    ObjectiveValidationError,
    ProposalProjection,
    project_proposal,
)
from bossman_shared.objective_store import (
    CompareAndSwapError,
    DuplicateProposal,
    DuplicateReservation,
    ObjectiveStore,
    ObjectiveStoreError,
)

REFUSED = (ObjectiveStoreError, ObjectiveValidationError)


def raw_spec():
    return {
        "schema_version": 1, "owner_id": "owner", "scope_id": "project",
        "objective_id": "build", "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": "local-build", "source_revision": "v1", "max_age_seconds": 30},
                    {"source_ref": "local-lint", "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "green", "source_ref": "local-build", "field": "passed",
                        "value_type": "boolean", "operator": "eq", "expected": True},
                       {"predicate_id": "clean", "source_ref": "local-lint", "field": "passed",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": 1000, "priority": 2, "allowed_triggers": ["source_change"],
        "permission_refs": ["owner-grant"], "conflict_keys": ["project-build"],
        "cooldown_seconds": 60,
        "limits": {"max_observations": 8, "max_missions": 2, "max_wall_seconds": 300,
                   "max_cost_usd": 5},
        "stop_conditions": ["owner-revocation", "budget-exhausted"],
    }


def observation(spec, source_ref, passed):
    return {"observation_id": f"obs-{source_ref}", "owner_id": "owner", "scope_id": "project",
            "objective_digest": spec.digest, "source_ref": source_ref, "source_revision": "v1",
            "observed_at": 90, "values": {"passed": passed}}


def revision_of(previous, **changes):
    raw = previous.to_dict()
    raw.update(revision=raw["revision"] + 1, previous_digest=previous.digest)
    raw.update(changes)
    return ObjectiveSpec.from_dict(raw, previous=previous)


def build_only(previous):
    """A revision that stops declaring the lint source (and its predicate)."""
    return revision_of(previous,
                       sources=[s for s in previous.to_dict()["sources"]
                                if s["source_ref"] == "local-build"],
                       predicates=[p for p in previous.to_dict()["predicates"]
                                   if p["source_ref"] == "local-build"])


@pytest.fixture
def spec():
    return ObjectiveSpec.from_dict(raw_spec())


@pytest.fixture
def db(tmp_path):
    return tmp_path / "objectives" / "v5.db"


@pytest.fixture
def store(db, spec):
    store = ObjectiveStore(db)
    store.create(spec)
    return store


def activate(store, now=100):
    state = store.get("build")
    return store.transition("build", "ACTIVE", now=now, owner_id="owner",
                            expected_version=state.version)


# ------------------------------------------------------------------ import

def test_import_is_draft_and_a_fresh_objective_enrolls_nothing(db, spec):
    store = ObjectiveStore(db)
    with pytest.raises(ObjectiveStoreError):
        store.create(spec, lifecycle="ACTIVE")
    assert store.list_objectives() == []
    state = store.create(spec)
    assert (state.lifecycle, state.condition, state.enrolled_sources) == ("DRAFT", "UNKNOWN", ())
    assert state.last_verified_evidence_ref is None and state.stopped is False
    assert (state.observations_used, state.missions_used) == (0, 0)
    assert store.get("build") == state
    with pytest.raises(ObjectiveStoreError):
        store.create(spec)


def test_import_requires_a_validated_spec_object(store):
    for candidate in (raw_spec(), None, "{}"):
        with pytest.raises(ObjectiveStoreError):
            store.create(candidate)


def test_in_memory_store_is_refused_because_it_cannot_survive_restart():
    with pytest.raises(ValueError) as excinfo:
        ObjectiveStore(":memory:")
    assert "restart" in str(excinfo.value)


# -------------------------------------------------------------- durability

def test_every_field_survives_a_restart(db, store, spec):
    state = activate(store)
    state = store.enroll_sources("build", ("local-build",), owner_id="owner",
                                 expected_version=state.version)
    state = store.set_condition("build", "SATISFIED", evidence_ref="ev-1",
                                expected_version=state.version)
    state = store.record_observation("build", observed_at=91.5, count=3,
                                     expected_version=state.version)
    state = store.record_mission_usage("build", missions=1, wall_seconds=12.5, cost_usd=0.25,
                                       expected_version=state.version)
    state = store.set_stopped("build", True, reason="budget", expected_version=state.version)
    store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                               objective_digest=spec.digest, objective_revision=1,
                               created_at=95.0, valid_until=120.0, payload={"a": 1})
    store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                       created_at=96.0, payload={"b": 2})

    reopened = ObjectiveStore(db)
    restored = reopened.get("build")
    assert restored == store.get("build")
    assert restored.lifecycle == "ACTIVE" and restored.condition == "SATISFIED"
    assert restored.last_verified_evidence_ref == "ev-1"
    assert restored.enrolled_sources == ("local-build",)
    assert (restored.observations_used, restored.last_observation_at) == (3, 91.5)
    assert (restored.missions_used, restored.wall_seconds_used, restored.cost_usd_used) == \
        (1, 12.5, 0.25)
    assert restored.last_proposal_at == 95.0 and restored.stopped is True
    assert reopened.get_spec("build").digest == spec.digest
    assert [r["reservation_id"] for r in reopened.open_reservations("build")] == ["r-1"]
    assert [e["event"] for e in reopened.journal("build")] == \
        [e["event"] for e in store.journal("build")]


def test_reopening_the_store_keeps_every_row(db, store, spec):
    store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                               objective_digest=spec.digest, objective_revision=1,
                               created_at=1.0, valid_until=2.0, payload={})
    before = store.journal("build")
    for _ in range(3):
        again = ObjectiveStore(db)
    assert again.journal("build") == before
    assert [s.objective_id for s in again.list_objectives()] == ["build"]
    with pytest.raises(DuplicateProposal):
        again.insert_proposal_once(proposal_id="p-1", objective_id="build",
                                   objective_digest=spec.digest, objective_revision=1,
                                   created_at=1.0, valid_until=2.0, payload={})


def test_a_newer_schema_version_is_refused_rather_than_downgraded(db, store):
    con = sqlite3.connect(db)
    con.execute("UPDATE v5_schema SET value='99' WHERE key='version'")
    con.commit()
    con.close()
    with pytest.raises(ObjectiveStoreError) as excinfo:
        ObjectiveStore(db)
    assert "newer schema" in str(excinfo.value)


# --------------------------------------------------------- compare-and-swap

MUTATORS = {
    "transition": lambda s, v: s.transition("build", "ACTIVE", now=100, owner_id="owner",
                                            expected_version=v),
    "enroll_sources": lambda s, v: s.enroll_sources("build", ("local-build",), owner_id="owner",
                                                    expected_version=v),
    "revise": lambda s, v: s.revise("build", revision_of(ObjectiveSpec.from_dict(raw_spec()),
                                                         priority=7),
                                    owner_id="owner", expected_version=v),
    "set_stopped": lambda s, v: s.set_stopped("build", True, reason="x", expected_version=v),
    "record_observation": lambda s, v: s.record_observation("build", observed_at=1.0, count=1,
                                                            expected_version=v),
    "set_condition": lambda s, v: s.set_condition("build", "DEVIATED", evidence_ref=None,
                                                  expected_version=v),
    "record_mission_usage": lambda s, v: s.record_mission_usage("build", missions=1,
                                                                wall_seconds=1.0, cost_usd=1.0,
                                                                expected_version=v),
}


@pytest.mark.parametrize("name", sorted(MUTATORS))
def test_a_stale_version_is_refused_by_every_mutator(store, name):
    before = store.get("build")
    for stale in (before.version - 1, before.version + 1, 0, 999):
        with pytest.raises(CompareAndSwapError):
            MUTATORS[name](store, stale)
    assert store.get("build") == before


@pytest.mark.parametrize("name", sorted(MUTATORS))
def test_two_callers_holding_the_same_version_leave_exactly_one_winner(store, name):
    version = store.get("build").version
    MUTATORS[name](store, version)
    with pytest.raises(CompareAndSwapError):
        MUTATORS[name](store, version)
    assert store.get("build").version == version + 1


def test_concurrent_mutators_never_lose_an_update(db, store):
    activate(store)
    successes, failures, lock = [], [], threading.Lock()

    def hammer(kind):
        local = ObjectiveStore(db)
        for _ in range(20):
            while True:
                version = local.get("build").version
                try:
                    if kind == 0:
                        local.record_observation("build", observed_at=1.0, count=1,
                                                 expected_version=version)
                    else:
                        local.record_mission_usage("build", missions=1, wall_seconds=0.5,
                                                   cost_usd=0.0, expected_version=version)
                except CompareAndSwapError as exc:
                    with lock:
                        failures.append(exc)
                    continue
                except sqlite3.OperationalError as exc:  # benign sqlite busy retry
                    with lock:
                        failures.append(exc)
                    continue
                with lock:
                    successes.append(kind)
                break

    threads = [threading.Thread(target=hammer, args=(i % 2,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(t.is_alive() for t in threads)
    final = ObjectiveStore(db).get("build")
    assert len(successes) == 120
    assert final.observations_used == successes.count(0)
    assert final.missions_used == successes.count(1)
    assert final.wall_seconds_used == pytest.approx(0.5 * successes.count(1))
    assert all(isinstance(exc, (CompareAndSwapError, sqlite3.OperationalError))
               for exc in failures)


# ------------------------------------------------------------------ lifecycle

def test_revocation_cannot_be_undone(store):
    state = activate(store)
    state = store.transition("build", "REVOKED", now=100, owner_id="owner",
                             expected_version=state.version)
    assert state.lifecycle == "REVOKED"
    for requested in ("ACTIVE", "PAUSED", "DRAFT", "EXPIRED"):
        with pytest.raises(REFUSED):
            store.transition("build", requested, now=100, owner_id="owner",
                             expected_version=state.version)
    assert store.get("build") == state


def test_revocation_survives_expiry(store):
    state = store.transition("build", "REVOKED", now=100, owner_id="owner",
                             expected_version=store.get("build").version)
    with pytest.raises(REFUSED):
        store.transition("build", "ACTIVE", now=5000, owner_id="owner",
                         expected_version=state.version)
    assert store.get("build").lifecycle == "REVOKED"
    assert store.transition("build", "REVOKED", now=5000, owner_id="owner",
                            expected_version=state.version).lifecycle == "REVOKED"


def test_an_expired_objective_cannot_go_active(store):
    version = store.get("build").version
    with pytest.raises(REFUSED):
        store.transition("build", "ACTIVE", now=5000, owner_id="owner",
                         expected_version=version)
    assert store.get("build").lifecycle == "DRAFT"
    state = store.transition("build", "EXPIRED", now=5000, owner_id="owner",
                             expected_version=version)
    assert state.lifecycle == "EXPIRED"


def test_expiry_is_not_resurrected_by_an_earlier_clock(store):
    state = store.transition("build", "EXPIRED", now=5000, owner_id="owner",
                             expected_version=store.get("build").version)
    for requested in ("ACTIVE", "PAUSED", "DRAFT"):
        with pytest.raises(REFUSED):
            store.transition("build", requested, now=1, owner_id="owner",
                             expected_version=state.version)
    assert store.get("build").lifecycle == "EXPIRED"


def test_lifecycle_transitions_require_the_owner_identity(store):
    with pytest.raises(REFUSED):
        store.transition("build", "ACTIVE", now=100, owner_id="mallory",
                         expected_version=store.get("build").version)
    assert store.get("build").lifecycle == "DRAFT"


def test_leaving_active_clears_the_condition_and_drops_the_evidence(db, store):
    state = activate(store)
    state = store.set_condition("build", "SATISFIED", evidence_ref="ev-1",
                                expected_version=state.version)
    state = store.transition("build", "PAUSED", now=100, owner_id="owner",
                             expected_version=state.version)
    assert (state.condition, state.last_verified_evidence_ref) == ("UNKNOWN", None)
    restored = ObjectiveStore(db).get("build")
    assert (restored.condition, restored.last_verified_evidence_ref) == ("UNKNOWN", None)
    back = store.transition("build", "ACTIVE", now=100, owner_id="owner",
                            expected_version=state.version)
    assert (back.condition, back.last_verified_evidence_ref) == ("UNKNOWN", None)


def test_unknown_objectives_are_refused_everywhere(store):
    with pytest.raises(ObjectiveStoreError):
        store.get("ghost")
    with pytest.raises(ObjectiveStoreError):
        store.get_spec("ghost")
    with pytest.raises(ObjectiveStoreError):
        store.transition("ghost", "ACTIVE", now=100, owner_id="owner", expected_version=1)
    with pytest.raises(ObjectiveStoreError):
        store.set_stopped("ghost", True, reason="x", expected_version=1)


# ------------------------------------------------------------------ revision

def test_revision_cannot_buy_a_fresh_budget(store, spec):
    state = store.record_observation("build", observed_at=5.0, count=4,
                                     expected_version=store.get("build").version)
    state = store.record_mission_usage("build", missions=2, wall_seconds=30.0, cost_usd=1.5,
                                       expected_version=state.version)
    state = store.revise("build", revision_of(spec, priority=9), owner_id="owner",
                         expected_version=state.version)
    assert (state.observations_used, state.missions_used) == (4, 2)
    assert (state.wall_seconds_used, state.cost_usd_used) == (30.0, 1.5)
    assert state.last_observation_at == 5.0
    stored = store.get("build")
    assert (stored.observations_used, stored.missions_used) == (4, 2)
    assert (stored.wall_seconds_used, stored.cost_usd_used) == (30.0, 1.5)


def test_revision_drops_the_condition_and_the_stale_evidence(store, spec):
    state = activate(store)
    state = store.set_condition("build", "SATISFIED", evidence_ref="ev-1",
                                expected_version=state.version)
    state = store.revise("build", revision_of(spec, priority=9), owner_id="owner",
                         expected_version=state.version)
    assert (state.condition, state.last_verified_evidence_ref) == ("UNKNOWN", None)
    assert state.revision == 2 and state.spec_digest != spec.digest
    assert store.get("build") == state
    assert store.get_spec("build").digest == state.spec_digest


def test_revision_narrows_enrollment_to_sources_it_still_declares(store, spec):
    state = store.enroll_sources("build", ("local-build", "local-lint"), owner_id="owner",
                                 expected_version=store.get("build").version)
    assert state.enrolled_sources == ("local-build", "local-lint")
    state = store.revise("build", build_only(spec), owner_id="owner",
                         expected_version=state.version)
    assert state.enrolled_sources == ("local-build",)
    assert store.get("build").enrolled_sources == ("local-build",)


@pytest.mark.parametrize("field", ["owner_id", "scope_id"])
def test_revision_cannot_change_owner_or_scope(store, field):
    forged = raw_spec()
    forged[field] = "mallory"
    first = ObjectiveSpec.from_dict(forged)
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.revise("build", revision_of(first), owner_id="owner",
                     expected_version=before.version)
    assert store.get("build") == before


def test_revision_must_chain_to_the_stored_predecessor(store, spec):
    other = ObjectiveSpec.from_dict({**raw_spec(), "priority": 4})
    before = store.get("build")
    for candidate in (revision_of(other), spec, revision_of(revision_of(spec))):
        with pytest.raises(ObjectiveStoreError):
            store.revise("build", candidate, owner_id="owner", expected_version=before.version)
    with pytest.raises(ObjectiveStoreError):
        store.revise("build", raw_spec(), owner_id="owner", expected_version=before.version)
    assert store.get("build") == before


def test_revision_requires_the_owner_identity(store, spec):
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.revise("build", revision_of(spec, priority=9), owner_id="mallory",
                     expected_version=before.version)
    assert store.get("build") == before


def test_a_revoked_objective_cannot_be_revised(store, spec):
    state = store.transition("build", "REVOKED", now=100, owner_id="owner",
                             expected_version=store.get("build").version)
    with pytest.raises(ObjectiveStoreError) as excinfo:
        store.revise("build", revision_of(spec, priority=9), owner_id="owner",
                     expected_version=state.version)
    assert "sticky" in str(excinfo.value)
    assert store.get("build") == state


# ---------------------------------------------------------------- enrollment

def test_only_sources_the_spec_declares_can_be_enrolled(store):
    before = store.get("build")
    for sources in (("elsewhere",), ("local-build", "elsewhere")):
        with pytest.raises(ObjectiveStoreError):
            store.enroll_sources("build", sources, owner_id="owner",
                                 expected_version=before.version)
    with pytest.raises(ObjectiveStoreError):
        store.enroll_sources("build", ["local-build"], owner_id="owner",
                             expected_version=before.version)
    with pytest.raises(ObjectiveStoreError):
        store.enroll_sources("build", (b"local-build",), owner_id="owner",
                             expected_version=before.version)
    assert store.get("build") == before


def test_enrollment_by_a_non_owner_is_refused(store):
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.enroll_sources("build", ("local-build",), owner_id="mallory",
                             expected_version=before.version)
    assert store.get("build").enrolled_sources == ()


def test_a_withdrawn_source_is_gone(db, store):
    state = store.enroll_sources("build", ("local-lint", "local-build"), owner_id="owner",
                                 expected_version=store.get("build").version)
    assert state.enrolled_sources == ("local-build", "local-lint")
    state = store.enroll_sources("build", ("local-build",), owner_id="owner",
                                 expected_version=state.version)
    assert state.enrolled_sources == ("local-build",)
    state = store.enroll_sources("build", (), owner_id="owner", expected_version=state.version)
    assert state.enrolled_sources == ()
    assert ObjectiveStore(db).get("build").enrolled_sources == ()


# ----------------------------------------------------------------- condition

@pytest.mark.parametrize("evidence", [None, "", "   ", 1, b"ev"])
def test_satisfied_requires_a_nonempty_evidence_ref(store, evidence):
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.set_condition("build", "SATISFIED", evidence_ref=evidence,
                            expected_version=before.version)
    assert store.get("build") == before


@pytest.mark.parametrize("condition", ["DEVIATED", "UNKNOWN"])
def test_deviated_and_unknown_need_no_evidence(store, condition):
    state = store.set_condition("build", condition, evidence_ref=None,
                                expected_version=store.get("build").version)
    assert (state.condition, state.last_verified_evidence_ref) == (condition, None)
    assert store.get("build") == state


@pytest.mark.parametrize("condition", ["satisfied", "GREEN", "", None, "ACTIVE"])
def test_an_unsupported_condition_is_refused(store, condition):
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.set_condition("build", condition, evidence_ref="ev-1",
                            expected_version=before.version)
    assert store.get("build") == before


# --------------------------------------------------------------------- usage

@pytest.mark.parametrize("count", [-1, True, False, 1.0, "1", None])
def test_observation_counts_must_be_nonnegative_integers(store, count):
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.record_observation("build", observed_at=1.0, count=count,
                                 expected_version=before.version)
    assert store.get("build") == before


@pytest.mark.parametrize("observed_at", [-1.0, float("nan"), float("inf"), True, "1", None])
def test_observation_times_must_be_nonnegative_finite_numbers(store, observed_at):
    before = store.get("build")
    with pytest.raises(ObjectiveStoreError):
        store.record_observation("build", observed_at=observed_at, count=1,
                                 expected_version=before.version)
    assert store.get("build") == before


@pytest.mark.parametrize("field", ["missions", "wall_seconds", "cost_usd"])
@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "1", None])
def test_mission_usage_must_be_nonnegative_finite_numbers(store, field, value):
    before = store.get("build")
    usage = {"missions": 1, "wall_seconds": 1.0, "cost_usd": 1.0}
    usage[field] = value
    with pytest.raises(ObjectiveStoreError):
        store.record_mission_usage("build", expected_version=before.version, **usage)
    assert store.get("build") == before


def test_stop_state_must_be_boolean(store):
    before = store.get("build")
    for stopped in (1, "yes", None):
        with pytest.raises(ObjectiveStoreError):
            store.set_stopped("build", stopped, reason="x", expected_version=before.version)
    assert store.get("build").stopped is False


# ------------------------------------------------------- proposals/reservations

def test_a_proposal_identity_is_recorded_exactly_once(db, store, spec):
    store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                               objective_digest=spec.digest, objective_revision=1,
                               created_at=10.0, valid_until=20.0, payload={"n": 1})
    with pytest.raises(DuplicateProposal):
        store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                                   objective_digest=spec.digest, objective_revision=1,
                                   created_at=30.0, valid_until=40.0, payload={"n": 2})
    con = sqlite3.connect(db)
    rows = con.execute("SELECT created_at FROM v5_proposals WHERE proposal_id='p-1'").fetchall()
    con.close()
    assert rows == [(10.0,)]
    assert store.get("build").last_proposal_at == 10.0


def test_a_second_reservation_for_one_proposal_is_refused_under_a_fresh_id(db, store):
    store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                       created_at=10.0, payload={})
    with pytest.raises(DuplicateReservation):
        store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                           created_at=11.0, payload={})
    with pytest.raises(DuplicateReservation):
        store.reserve_once(reservation_id="r-2", objective_id="build", proposal_id="p-1",
                           created_at=12.0, payload={})
    assert [r["reservation_id"] for r in store.open_reservations("build")] == ["r-1"]
    con = sqlite3.connect(db)
    count = con.execute("SELECT count(*) FROM v5_reservations").fetchone()[0]
    con.close()
    assert count == 1


def test_settlement_is_final(store):
    store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                       created_at=10.0, payload={})
    store.settle_reservation("r-1", "COMMITTED")
    with pytest.raises(ObjectiveStoreError) as excinfo:
        store.settle_reservation("r-1", "RELEASED")
    assert "already settled" in str(excinfo.value)
    assert store.open_reservations("build") == []


@pytest.mark.parametrize("state", ["RESERVED", "committed", "", None, "CANCELLED"])
def test_only_committed_or_released_settles_a_reservation(store, state):
    store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                       created_at=10.0, payload={})
    with pytest.raises(ObjectiveStoreError):
        store.settle_reservation("r-1", state)
    assert [r["reservation_id"] for r in store.open_reservations("build")] == ["r-1"]


def test_an_unknown_reservation_cannot_be_settled(store):
    with pytest.raises(ObjectiveStoreError) as excinfo:
        store.settle_reservation("nope", "COMMITTED")
    assert "unknown reservation" in str(excinfo.value)


def test_open_reservations_are_exactly_the_unsettled_ones_after_a_restart(db, store):
    for index in range(3):
        store.reserve_once(reservation_id=f"r-{index}", objective_id="build",
                           proposal_id=f"p-{index}", created_at=float(index),
                           payload={"index": index})
    store.settle_reservation("r-1", "RELEASED")
    reopened = ObjectiveStore(db)
    still_open = reopened.open_reservations("build")
    assert [r["reservation_id"] for r in still_open] == ["r-0", "r-2"]
    assert still_open[0]["payload"] == {"index": 0}
    assert still_open[0]["proposal_id"] == "p-0"
    assert reopened.open_reservations("other") == []


# ------------------------------------------------------------------ snapshot

def test_proposal_snapshot_matches_the_projection_contract(store, spec):
    state = activate(store)
    state = store.enroll_sources("build", ("local-build", "local-lint"), owner_id="owner",
                                 expected_version=state.version)
    snapshot = state.proposal_snapshot()
    assert set(snapshot) == {"owner_id", "scope_id", "objective_digest", "lifecycle",
                             "enrolled_sources", "observations_used", "missions_used",
                             "wall_seconds_used", "cost_usd_used", "last_proposal_at", "stopped"}
    observations = [observation(spec, "local-build", False),
                    observation(spec, "local-lint", True)]
    projection = project_proposal(store.get_spec("build"), observations, now=100,
                                  snapshot=snapshot, trigger="source_change")
    assert isinstance(projection, ProposalProjection)

    stopped = store.set_stopped("build", True, reason="budget", expected_version=state.version)
    assert project_proposal(store.get_spec("build"), observations, now=100,
                            snapshot=stopped.proposal_snapshot(),
                            trigger="source_change") is None
    assert json.loads(json.dumps(stopped.proposal_snapshot())) == stopped.proposal_snapshot()


def test_a_draft_snapshot_projects_nothing_without_raising(store, spec):
    projection = project_proposal(store.get_spec("build"),
                                  [observation(spec, "local-build", False)], now=100,
                                  snapshot=store.get("build").proposal_snapshot(),
                                  trigger="source_change")
    assert projection is None


# ------------------------------------------------------------------- journal

def test_the_journal_records_the_objective_history_and_survives_a_restart(db, store, spec):
    state = activate(store)
    state = store.enroll_sources("build", ("local-build",), owner_id="owner",
                                 expected_version=state.version)
    state = store.revise("build", revision_of(spec, priority=9), owner_id="owner",
                         expected_version=state.version)
    state = store.set_condition("build", "DEVIATED", evidence_ref=None,
                                expected_version=state.version)
    state = store.set_stopped("build", True, reason="budget", expected_version=state.version)
    store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                               objective_digest=state.spec_digest, objective_revision=2,
                               created_at=10.0, valid_until=20.0, payload={})
    store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                       created_at=11.0, payload={})
    store.settle_reservation("r-1", "COMMITTED")

    entries = ObjectiveStore(db).journal("build")
    # Бронь здесь создана с пустым payload, поэтому закрытие честно отмечает,
    # что списывать по ней нечего: счётчик миссий вырос, а стоимость и время —
    # нет. Молчаливое «потрачено ноль» неотличимо от измеренного нуля.
    assert [e["event"] for e in entries] == ["imported", "lifecycle", "enrollment", "revised",
                                             "condition", "stop", "proposal", "reserved",
                                             "usage_estimate_unusable", "settled"]
    detail = {e["event"]: e["detail"] for e in entries}
    assert detail["lifecycle"] == "DRAFT->ACTIVE"
    assert detail["enrollment"] == "local-build"
    assert detail["revised"] == "1->2"
    assert detail["settled"] == "r-1:COMMITTED"
    assert "no usable estimate" in detail["usage_estimate_unusable"]
    assert all(isinstance(e["at"], (int, float)) for e in entries)


def test_a_refused_write_leaves_no_journal_entry(store):
    before = store.journal("build")
    with pytest.raises(REFUSED):
        store.transition("build", "ACTIVE", now=100, owner_id="mallory",
                         expected_version=store.get("build").version)
    with pytest.raises(CompareAndSwapError):
        store.set_condition("build", "DEVIATED", evidence_ref=None, expected_version=999)
    assert store.journal("build") == before


def test_listing_filters_by_owner_and_lifecycle(store, spec):
    assert [s.objective_id for s in store.list_objectives(owner_id="owner")] == ["build"]
    assert store.list_objectives(owner_id="mallory") == []
    assert [s.objective_id for s in store.list_objectives(lifecycle="DRAFT")] == ["build"]
    assert store.list_objectives(lifecycle="ACTIVE") == []
    with pytest.raises(ObjectiveStoreError):
        store.list_objectives(lifecycle="GREEN")


def test_a_revised_objective_is_still_readable_and_revisable(db, store, spec):
    state = store.revise("build", revision_of(spec, priority=9), owner_id="owner",
                         expected_version=store.get("build").version)
    second = store.get_spec("build")
    assert second.digest == state.spec_digest and second.to_dict()["revision"] == 2
    state = store.transition("build", "ACTIVE", now=100, owner_id="owner",
                             expected_version=state.version)
    state = store.revise("build", revision_of(second, priority=11), owner_id="owner",
                         expected_version=state.version)
    assert state.revision == 3
    reopened = ObjectiveStore(db)
    assert reopened.get_spec("build").digest == state.spec_digest
    assert reopened.get_spec("build").to_dict()["previous_digest"] == second.digest


def test_a_tampered_stored_spec_is_refused_rather_than_served(db, store):
    forged = ObjectiveSpec.from_dict({**raw_spec(), "priority": 41}).to_json()
    con = sqlite3.connect(db)
    con.execute("UPDATE v5_objectives SET spec_json=? WHERE objective_id='build'", (forged,))
    con.commit()
    con.close()
    reopened = ObjectiveStore(db)
    with pytest.raises(ObjectiveStoreError) as excinfo:
        reopened.get_spec("build")
    assert "digest" in str(excinfo.value)
    with pytest.raises(ObjectiveStoreError):
        reopened.transition("build", "ACTIVE", now=100, owner_id="owner",
                            expected_version=reopened.get("build").version)
