"""V5 crash matrix, reconciliation and long-horizon resume.

Every row asserts the same two things: no duplicate irreversible effect, and the
resulting condition is UNKNOWN unless a fresh verified re-observation says
otherwise. The store is always a real file under tmp_path -- an in-memory store
cannot demonstrate restart durability.
"""
import pytest

from bossman_shared import evidence
from bossman_shared.objective_reconcile import (
    REASON_EVIDENCE_BINDING,
    REASON_EVIDENCE_UNSIGNED,
    REASON_FRESH,
    REASON_MISSION_UNVERIFIED,
    REASON_PRE_EFFECT,
    REASON_REOBSERVE_FAILED,
    apply_unknown_dominance,
    bind_evidence,
    observation_digests,
    reconcile_after_mission,
)
from bossman_shared.objective_recovery import (
    APPLIED,
    COMMITTED,
    IDEMPOTENT,
    IRREVERSIBLE,
    NOT_APPLIED,
    PARKED,
    REASON_AMBIGUOUS,
    REASON_BUDGET_EXHAUSTED,
    REASON_IRREVERSIBLE,
    RELEASED,
    ROLLBACK_ORDER,
    UNKNOWN,
    bounded_retry,
    prepare_rollback,
    recover,
    resume_point,
)
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore

NOW = 100.0
EFFECT_AT = 94.0
SIGNER = "bossman_v3.verifier"


@pytest.fixture(autouse=True)
def evidence_key(tmp_path, monkeypatch):
    """Never touch a real home directory: the key lives and dies with tmp_path."""
    monkeypatch.setenv(evidence.ENV_KEY_FILE, str(tmp_path / "evidence.key"))
    monkeypatch.setenv(evidence.ENV_DATA_DIR, str(tmp_path / "data"))
    evidence.reset_cache()
    yield
    evidence.reset_cache()


def raw_spec():
    return {
        "schema_version": 1, "owner_id": "owner", "scope_id": "project",
        "objective_id": "build", "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": "local-build", "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "green", "source_ref": "local-build", "field": "passed",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": 1000, "priority": 2, "allowed_triggers": ["source_change"],
        "permission_refs": ["owner-grant"], "conflict_keys": ["project-build"],
        "cooldown_seconds": 60,
        "limits": {"max_observations": 8, "max_missions": 4, "max_wall_seconds": 300,
                   "max_cost_usd": 1},
        "stop_conditions": ["owner-revocation"],
    }


def make_store(tmp_path, name="objectives.db"):
    store = ObjectiveStore(tmp_path / name)
    spec = ObjectiveSpec.from_dict(raw_spec())
    state = store.create(spec)
    state = store.transition("build", "ACTIVE", now=NOW, owner_id="owner",
                             expected_version=state.version)
    store.enroll_sources("build", ("local-build",), owner_id="owner",
                         expected_version=state.version)
    return store, spec


def observation(spec, *, passed=True, observed_at=95.0, obs_id="obs-1"):
    return {"observation_id": obs_id, "owner_id": "owner", "scope_id": "project",
            "objective_digest": spec.digest, "source_ref": "local-build",
            "source_revision": "v1", "observed_at": observed_at, "values": {"passed": passed}}


def signed_evidence(store, batch, *, mission_id="m-1", reservation_id="r-1",
                    effect_at=EFFECT_AT, ref=None):
    """Улика reconcile теперь НЕСЁТ разрешаемую ссылку, а не выдуманную строку.

    Раньше здесь стояло `ref="ev-1"`: строка, которую никто не резолвил, и
    именно она доезжала до `set_condition` как «доказательство». Ссылка
    чеканится в хранилище и привязана к цели, условию, редакции и прогону.
    """
    state = store.get("build")
    if ref is None:
        ref = store.record_condition_evidence("build", condition="SATISFIED",
                                              run_id=reservation_id)
    payload = bind_evidence(
        objective_id="build", objective_digest=state.spec_digest,
        objective_revision=state.revision, mission_id=mission_id,
        reservation_id=reservation_id, evidence_ref=ref, effect_at=effect_at,
        digests=observation_digests(batch))
    return {**payload, **evidence.sign_fields(payload, signer=SIGNER)}


def open_reservation(store, *, effect_class=IRREVERSIBLE, reservation_id="r-1",
                     proposal_id="p-1", **payload):
    store.insert_proposal_once(proposal_id=proposal_id, objective_id="build",
                               objective_digest=store.get("build").spec_digest,
                               objective_revision=1, created_at=90.0, valid_until=200.0,
                               payload={"trigger": "source_change"})
    store.reserve_once(reservation_id=reservation_id, objective_id="build",
                       proposal_id=proposal_id, created_at=91.0,
                       payload={"effect_class": effect_class, "wall_seconds": 2.0,
                                "cost_usd": 0.01, **payload})


class Probe:
    """Observer-backed effect probe that also counts dispatch attempts (zero)."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def __call__(self, reservation):
        self.calls += 1
        return self.answer


# ------------------------------------------------------------------ crash matrix

def test_crash_before_dispatch_leaves_nothing_to_replay(tmp_path):
    store, _ = make_store(tmp_path)
    store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                               objective_digest=store.get("build").spec_digest,
                               objective_revision=1, created_at=90.0, valid_until=200.0,
                               payload={})
    probe = Probe(APPLIED)
    report = recover(store, "build", now=NOW, is_effect_applied=probe)
    assert report.outcomes == () and probe.calls == 0 and not report.blocked
    assert store.get("build").condition == "UNKNOWN"
    assert store.get("build").missions_used == 0


def test_crash_after_dispatch_before_external_effect_parks_irreversible(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(NOT_APPLIED))
    outcome = report.outcomes[0]
    assert (outcome.disposition, outcome.reason) == (PARKED, REASON_IRREVERSIBLE)
    assert report.blocked and outcome.requires_owner
    # Parked means untouched: nothing settled, so nothing may be replayed.
    assert [r["reservation_id"] for r in store.open_reservations("build")] == ["r-1"]
    assert store.get("build").condition == "UNKNOWN"
    assert store.get("build").missions_used == 0


def test_crash_after_dispatch_releases_idempotent_for_fresh_admission(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IDEMPOTENT)
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(NOT_APPLIED))
    assert report.outcomes[0].disposition == RELEASED and not report.blocked
    assert store.open_reservations("build") == []
    assert store.get("build").missions_used == 0
    assert store.get("build").condition == "UNKNOWN"


def test_crash_after_external_effect_before_journal_commits_once(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(APPLIED))
    assert report.outcomes[0].disposition == COMMITTED and not report.blocked
    state = store.get("build")
    assert state.missions_used == 1 and state.condition == "UNKNOWN"
    assert store.open_reservations("build") == []


def test_crash_after_journal_before_receipt_no_duplicate_on_second_recover(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)
    recover(store, "build", now=NOW, is_effect_applied=Probe(APPLIED))
    probe = Probe(APPLIED)
    again = recover(store, "build", now=NOW, is_effect_applied=probe)
    # The reservation is gone, so a replayed recovery cannot charge or re-apply.
    assert again.outcomes == () and probe.calls == 0
    assert store.get("build").missions_used == 1
    assert store.get("build").condition == "UNKNOWN"


def test_crash_after_approval_without_effect_parks(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE, approval="owner-approved")
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(NOT_APPLIED))
    # An approval authorizes an attempt, never a replay of an unconfirmed effect.
    assert report.outcomes[0].disposition == PARKED and report.blocked
    assert len(store.open_reservations("build")) == 1
    assert store.get("build").condition == "UNKNOWN"


def test_crash_during_verification_yields_unknown_not_satisfied(tmp_path):
    store, spec = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)
    recover(store, "build", now=NOW, is_effect_applied=Probe(APPLIED))
    batch = [observation(spec)]
    result = reconcile_after_mission(
        store, "build", mission_verified=False, evidence=signed_evidence(store, batch),
        reobserve=lambda: batch, now=NOW, expected_version=store.get("build").version)
    assert (result.condition, result.reason) == ("UNKNOWN", REASON_MISSION_UNVERIFIED)
    assert store.get("build").condition == "UNKNOWN"
    assert store.get("build").missions_used == 1


def test_crash_during_finalization_downgrades_stored_satisfied(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec)]
    record = signed_evidence(store, batch)
    reconcile_after_mission(store, "build", mission_verified=True, evidence=record,
                            reobserve=lambda: batch, now=NOW,
                            expected_version=store.get("build").version)
    assert store.get("build").condition == "SATISFIED"
    open_reservation(store, effect_class=IRREVERSIBLE, reservation_id="r-2", proposal_id="p-2")
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(UNKNOWN))
    assert report.condition == "UNKNOWN" and report.blocked
    state = store.get("build")
    # The prior verified reference survives the downgrade: resume needs a fact.
    assert state.condition == "UNKNOWN"
    assert state.last_verified_evidence_ref == record["evidence_ref"]
    assert len(store.open_reservations("build")) == 1


# --------------------------------------------------- reconciliation invariants

def test_verified_fresh_reobservation_sets_satisfied(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec)]
    record = signed_evidence(store, batch)
    result = reconcile_after_mission(
        store, "build", mission_verified=True, evidence=record,
        reobserve=lambda: batch, now=NOW, expected_version=store.get("build").version,
        mission_id="m-1", reservation_id="r-1")
    assert (result.condition, result.reason) == ("SATISFIED", REASON_FRESH)
    assert result.evidence_refs == (record["evidence_ref"],) and result.used_observations
    state = store.get("build")
    assert state.condition == "SATISFIED" and state.observations_used == 1


def test_unverified_mission_never_yields_satisfied(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec)]
    calls = []

    def reobserve():
        calls.append(1)
        return batch

    result = reconcile_after_mission(
        store, "build", mission_verified=False, evidence=signed_evidence(store, batch),
        reobserve=reobserve, now=NOW, expected_version=store.get("build").version)
    assert result.condition == "UNKNOWN" and not calls
    assert store.get("build").condition == "UNKNOWN"


def test_tool_success_without_post_state_observation_is_unknown(tmp_path):
    store, _ = make_store(tmp_path)
    result = reconcile_after_mission(
        store, "build", mission_verified=True, evidence=None, reobserve=lambda: [],
        now=NOW, expected_version=store.get("build").version)
    assert (result.condition, result.reason) == ("UNKNOWN", REASON_REOBSERVE_FAILED)
    assert store.get("build").condition == "UNKNOWN"


def test_tampered_evidence_downgrades_to_unknown(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec)]
    tampered = {**signed_evidence(store, batch), "evidence_ref": "ev-forged"}
    result = reconcile_after_mission(
        store, "build", mission_verified=True, evidence=tampered, reobserve=lambda: batch,
        now=NOW, expected_version=store.get("build").version)
    assert (result.condition, result.reason) == ("UNKNOWN", REASON_EVIDENCE_UNSIGNED)
    assert result.evidence_refs == ()
    assert store.get("build").condition == "UNKNOWN"


def test_evidence_binding_nothing_proves_nothing(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec)]
    other = signed_evidence(store, [observation(spec, obs_id="obs-other")])
    result = reconcile_after_mission(
        store, "build", mission_verified=True, evidence=other, reobserve=lambda: batch,
        now=NOW, expected_version=store.get("build").version)
    assert (result.condition, result.reason) == ("UNKNOWN", REASON_EVIDENCE_BINDING)
    assert store.get("build").condition == "UNKNOWN"


def test_pre_effect_observation_refused_as_post_effect_proof(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec, observed_at=90.0)]
    record = signed_evidence(store, batch, effect_at=95.0)
    result = reconcile_after_mission(
        store, "build", mission_verified=True, evidence=record, reobserve=lambda: batch,
        now=NOW, expected_version=store.get("build").version)
    assert (result.condition, result.reason) == ("UNKNOWN", REASON_PRE_EFFECT)
    assert store.get("build").condition == "UNKNOWN"


def test_unknown_dominance_helper_never_upgrades():
    assert apply_unknown_dominance("SATISFIED", ()) == ("SATISFIED", REASON_FRESH)
    assert apply_unknown_dominance("UNKNOWN", ())[0] == "UNKNOWN"
    for gate in ("mission_verified", "evidence_verified", "observed_after_effect"):
        for evaluated in ("SATISFIED", "DEVIATED"):
            assert apply_unknown_dominance(evaluated, ((gate, False),))[0] == "UNKNOWN"
    assert apply_unknown_dominance("GREEN", ())[0] == "UNKNOWN"


# ------------------------------------------------------- parking, retry, resume

def test_irreversible_unknown_parks_rather_than_replays(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(UNKNOWN))
    outcome = report.outcomes[0]
    assert (outcome.disposition, outcome.reason) == (PARKED, REASON_AMBIGUOUS)
    assert report.parked == (outcome,) and report.blocked
    assert len(store.open_reservations("build")) == 1
    assert store.get("build").missions_used == 0


def test_failing_probe_answers_unknown_and_parks(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)

    def broken(_reservation):
        raise RuntimeError("observer down")

    report = recover(store, "build", now=NOW, is_effect_applied=broken)
    assert report.outcomes[0].answer == UNKNOWN and report.blocked
    assert len(store.open_reservations("build")) == 1


def test_missing_effect_class_defaults_to_irreversible(tmp_path):
    store, _ = make_store(tmp_path)
    store.insert_proposal_once(proposal_id="p-1", objective_id="build",
                               objective_digest=store.get("build").spec_digest,
                               objective_revision=1, created_at=90.0, valid_until=200.0,
                               payload={})
    store.reserve_once(reservation_id="r-1", objective_id="build", proposal_id="p-1",
                       created_at=91.0, payload={})
    report = recover(store, "build", now=NOW, is_effect_applied=Probe(NOT_APPLIED))
    assert report.outcomes[0].effect_class == IRREVERSIBLE
    assert report.outcomes[0].disposition == PARKED


def test_retry_budget_exhaustion_returns_blocked_not_loop():
    first = bounded_retry(0, budget=2)
    assert first.allowed and first.attempt == 1 and not first.blocked
    assert bounded_retry(1, budget=2).attempt == 2
    exhausted = bounded_retry(2, budget=2)
    assert not exhausted.allowed and exhausted.blocked
    assert exhausted.reason == REASON_BUDGET_EXHAUSTED
    assert not bounded_retry(9, budget=2).allowed
    for bad in (0, -1):
        with pytest.raises(ValueError):
            bounded_retry(0, budget=bad)
    with pytest.raises(ValueError):
        bounded_retry(-1, budget=2)


def test_resume_point_after_restart_reports_last_verified_state(tmp_path):
    store, spec = make_store(tmp_path)
    batch = [observation(spec)]
    record = signed_evidence(store, batch)
    reconcile_after_mission(store, "build", mission_verified=True,
                            evidence=record, reobserve=lambda: batch,
                            now=NOW, expected_version=store.get("build").version)
    open_reservation(store, effect_class=IDEMPOTENT, reservation_id="r-2", proposal_id="p-2")
    # Simulated restart: a second store object over the same durable file.
    restarted = ObjectiveStore(tmp_path / "objectives.db")
    point = resume_point(restarted, "build")
    assert point.has_verified_state and point.condition == "SATISFIED"
    assert point.last_verified_evidence_ref == record["evidence_ref"]
    assert point.last_observation_at == 95.0 and point.observations_used == 1
    assert point.open_reservations == ("r-2",)
    assert point.lifecycle == "ACTIVE" and point.version == restarted.get("build").version


def test_resume_point_reports_no_verified_state_when_unknown(tmp_path):
    store, _ = make_store(tmp_path)
    point = resume_point(store, "build")
    assert not point.has_verified_state and point.last_verified_evidence_ref is None
    assert point.missions_used == 0 and point.open_reservations == ()


def test_rollback_plan_is_ordered_data_and_executes_nothing(tmp_path):
    store, _ = make_store(tmp_path)
    open_reservation(store, effect_class=IRREVERSIBLE)
    before = store.get("build")
    plan = prepare_rollback(store, "build", now=NOW)
    assert [s.action for s in plan.steps] == [a for a, _ in ROLLBACK_ORDER]
    assert [s.order for s in plan.steps] == [1, 2, 3, 4, 5]
    assert plan.open_reservations == ("r-1",) and not plan.executed
    snapshot = plan.snapshot()
    assert snapshot["condition"] == "UNKNOWN" and snapshot["version"] == before.version
    # Preparing a rollback must not settle, dispatch or advance anything.
    assert store.get("build") == before
    assert len(store.open_reservations("build")) == 1
