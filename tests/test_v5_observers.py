"""V5 observer and world-state tests: determinism, freshness and refusal.

These tests exercise the sensing edge only. Nothing here claims admission,
runtime or acceptance; a FRESH fact is evidence, never an authorization.
"""
from __future__ import annotations

import pytest

from bossman_shared.objective_observer import (
    DirectoryStateObserver,
    EnrolledSource,
    FileStateObserver,
    ObservationError,
    ScheduledCheckObserver,
    collect,
    should_observe,
    should_observe_batch,
)
from bossman_shared.objective_spec import ObjectiveSpec, evaluate
from bossman_shared.objective_store import ObjectiveStore
from bossman_shared.objective_world_state import (
    UNKNOWN,
    FactRead,
    WorldFact,
    WorldStateError,
    WorldStateProjection,
    fact_from_observation,
)

OWNER = "owner"
SCOPE = "project"
SOURCE = "report-file"


def raw_spec(*, expected=True, field="exists", value_type="boolean", operator="eq"):
    return {
        "schema_version": 1, "owner_id": OWNER, "scope_id": SCOPE,
        "objective_id": "keep-report", "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": SOURCE, "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "present", "source_ref": SOURCE, "field": field,
                        "value_type": value_type, "operator": operator, "expected": expected}],
        "expires_at": 10_000, "priority": 1, "allowed_triggers": ["source_change"],
        "permission_refs": ["owner-grant"], "conflict_keys": ["report"],
        "cooldown_seconds": 5,
        "limits": {"max_observations": 3, "max_missions": 1, "max_wall_seconds": 60,
                   "max_cost_usd": 0},
        "stop_conditions": ["owner-revocation"],
    }


def enrollment(spec, *, source_ref=SOURCE, source_revision="v1", digest=None):
    return EnrolledSource(source_ref=source_ref, source_revision=source_revision,
                          owner_id=OWNER, scope_id=SCOPE,
                          objective_digest=digest or spec.digest)


def enrolled_state(tmp_path, spec, *, sources=(SOURCE,)):
    store = ObjectiveStore(tmp_path / "objectives.sqlite3")
    state = store.create(spec)
    state = store.enroll_sources(state.objective_id, tuple(sources), owner_id=OWNER,
                                 expected_version=state.version)
    return store, state


def run(spec, records, **changes):
    options = dict(now=100.0, lifecycle="ACTIVE", enrolled_sources=(SOURCE,),
                   trigger="source_change")
    options.update(changes)
    return evaluate(spec, records, **options)


# ------------------------------------------------------------------ observers


def test_file_observation_is_deterministic_and_content_bound(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observer = FileStateObserver(enrollment(spec), target)
    first, second = observer.observe(now=100.0), observer.observe(now=100.0)
    assert first.observation_id == second.observation_id
    assert first.values["exists"] is True and first.values["size_bytes"] == 5
    target.write_text("alphb", encoding="utf-8")
    changed = observer.observe(now=100.0)
    assert changed.observation_id != first.observation_id
    assert changed.values["sha256"] != first.values["sha256"]


def test_missing_file_is_reported_absent_not_omitted(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    observer = FileStateObserver(enrollment(spec), tmp_path / "gone.txt")
    values = observer.observe(now=100.0).values
    assert values == {"exists": False, "sha256": None, "size_bytes": 0}


def test_record_carries_exactly_the_evaluate_fields(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observation = FileStateObserver(enrollment(spec), target).observe(now=100.0)
    assert set(observation.record()) == {
        "observation_id", "owner_id", "scope_id", "objective_digest",
        "source_ref", "source_revision", "observed_at", "values"}
    assert "provenance" not in observation.record()
    assert observation.provenance["kind"] == "local_file"
    assert len(observation.provenance_digest) == 64


def test_observer_requires_an_explicit_enrollment(tmp_path):
    with pytest.raises(ObservationError):
        FileStateObserver("not-an-enrollment", tmp_path / "x")  # type: ignore[arg-type]


def test_file_observer_refuses_to_treat_a_symlink_as_the_named_file(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"SKIP_HOST: symlink privilege unavailable on this host: {exc}")
    values = FileStateObserver(enrollment(spec), link).observe(now=100.0).values
    assert values["exists"] is False and values["sha256"] is None


def test_directory_observation_is_sorted_bounded_and_deterministic(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    for name in ("b.txt", "a.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")
    (root / "sub" / "d.txt").write_text("d", encoding="utf-8")
    observer = DirectoryStateObserver(enrollment(spec), root)
    first = observer.observe(now=100.0)
    assert first.values["entry_count"] == 4 and first.values["truncated"] is False
    assert first.observation_id == observer.observe(now=100.0).observation_id
    bounded = DirectoryStateObserver(enrollment(spec), root, max_entries=2).observe(now=100.0)
    assert bounded.values["entry_count"] == 2 and bounded.values["truncated"] is True


def test_directory_observer_refuses_symlink_escape(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    clean = DirectoryStateObserver(enrollment(spec), root).observe(now=100.0)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (root / "escape").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"SKIP_HOST: symlink privilege unavailable on this host: {exc}")
    escaped = DirectoryStateObserver(enrollment(spec), root).observe(now=100.0)
    assert escaped.values["escaped_symlinks"] == 1
    assert escaped.values["symlinks_skipped"] == 1
    assert escaped.values["tree_digest"] == clean.values["tree_digest"]


def test_scheduled_check_refuses_non_json_values(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    good = ScheduledCheckObserver(enrollment(spec), lambda: {"passed": True, "count": 2})
    assert good.observe(now=100.0).values == {"passed": True, "count": 2}
    bad = ScheduledCheckObserver(enrollment(spec), lambda: {"handle": object()})
    with pytest.raises(ObservationError):
        bad.observe(now=100.0)
    listy = ScheduledCheckObserver(enrollment(spec), lambda: ["passed"])
    with pytest.raises(ObservationError):
        listy.observe(now=100.0)


# ------------------------------------------------------------------ collection


def test_unenrolled_source_is_dropped(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    _, state = enrolled_state(tmp_path, spec, sources=())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    batch = collect([FileStateObserver(enrollment(spec), target)], state=state,
                    max_observations=3, now=100.0)
    assert batch.observations == ()
    assert batch.dropped == ((SOURCE, "not_enrolled"),)


def test_duplicate_source_in_one_batch_refuses_every_claimant(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    _, state = enrolled_state(tmp_path, spec)
    one, two = tmp_path / "one.txt", tmp_path / "two.txt"
    one.write_text("1", encoding="utf-8")
    two.write_text("2", encoding="utf-8")
    batch = collect([FileStateObserver(enrollment(spec), one),
                     FileStateObserver(enrollment(spec), two)],
                    state=state, max_observations=3, now=100.0)
    assert batch.observations == ()
    assert batch.dropped == ((SOURCE, "duplicate_source"), (SOURCE, "duplicate_source"))


def test_stale_objective_digest_binding_is_dropped(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    _, state = enrolled_state(tmp_path, spec)
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    stale = enrollment(spec, digest="0" * 64)
    batch = collect([FileStateObserver(stale, target)], state=state,
                    max_observations=3, now=100.0)
    assert batch.dropped == ((SOURCE, "identity_mismatch"),)


def test_batch_never_exceeds_the_remaining_observation_budget(tmp_path):
    raw = raw_spec()
    raw["sources"].append({"source_ref": "other", "source_revision": "v1",
                           "max_age_seconds": 30})
    spec = ObjectiveSpec.from_dict(raw)
    store, state = enrolled_state(tmp_path, spec, sources=(SOURCE, "other"))
    state = store.record_observation(state.objective_id, observed_at=90.0, count=2,
                                     expected_version=state.version)
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observers = [FileStateObserver(enrollment(spec), target),
                 FileStateObserver(enrollment(spec, source_ref="other"), target)]
    batch = collect(observers, state=state, max_observations=3, now=100.0)
    assert len(batch.observations) == 1
    assert batch.dropped == ((SOURCE, "batch_quota"),)
    exhausted = store.record_observation(state.objective_id, observed_at=95.0, count=1,
                                         expected_version=state.version)
    empty = collect(observers, state=exhausted, max_observations=3, now=100.0)
    assert empty.observations == ()
    assert {reason for _, reason in empty.dropped} == {"quota_exhausted"}


# ------------------------------------------------------------ no-change gating


def test_should_observe_reports_no_change_when_the_world_is_steady(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    _, state = enrolled_state(tmp_path, spec)
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observer = FileStateObserver(enrollment(spec), target)
    first = observer.observe(now=100.0)
    assert should_observe(first, last_value_digest=None).reason == "first_observation"
    later = observer.observe(now=160.0)
    verdict = should_observe(later, last_value_digest=first.value_digest)
    assert verdict.changed is False and verdict.reason == "no_change"
    target.write_text("beta", encoding="utf-8")
    after = observer.observe(now=200.0)
    assert should_observe(after, last_value_digest=first.value_digest).changed is True
    batch = collect([observer], state=state, max_observations=3, now=200.0)
    assert [v.changed for v in should_observe_batch(batch, batch.value_digests())] == [False]


# ------------------------------------------------------------ end-to-end evaluate


def test_end_to_end_satisfied_from_a_real_file(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    _, state = enrolled_state(tmp_path, spec)
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    batch = collect([FileStateObserver(enrollment(spec), target)], state=state,
                    max_observations=3, now=100.0)
    result = run(spec, batch.records())
    assert result.condition == "SATISFIED"
    assert result.predicate_results == (("present", "SATISFIED", "fresh_observation"),)
    assert result.admission_allowed is False


def test_end_to_end_deviated_when_the_named_file_disappears(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    _, state = enrolled_state(tmp_path, spec)
    batch = collect([FileStateObserver(enrollment(spec), tmp_path / "gone.txt")],
                    state=state, max_observations=3, now=100.0)
    result = run(spec, batch.records())
    assert result.condition == "DEVIATED" and result.proposal_candidate is True


def test_wrong_source_revision_is_unknown(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observation = FileStateObserver(enrollment(spec, source_revision="v2"), target).observe(now=100.0)
    result = run(spec, [observation.record()])
    assert result.condition == "UNKNOWN"
    assert result.predicate_results[0][2] == "source_revision_mismatch"


def test_wrong_objective_digest_is_unknown(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observation = FileStateObserver(enrollment(spec, digest="0" * 64), target).observe(now=100.0)
    result = run(spec, [observation.record()])
    assert result.condition == "UNKNOWN"
    assert result.predicate_results[0][2] == "identity_mismatch"


def test_stale_and_unenrolled_observations_are_unknown(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observation = FileStateObserver(enrollment(spec), target).observe(now=10.0)
    assert run(spec, [observation.record()]).predicate_results[0][2] == "stale"
    fresh = FileStateObserver(enrollment(spec), target).observe(now=100.0)
    assert run(spec, [fresh.record()], enrolled_sources=()).predicate_results[0][2] == "not_enrolled"


# ---------------------------------------------------------------- world state


def fact(**changes):
    values = dict(key="build_green", value=True, source_ref=SOURCE, scope_id=SCOPE,
                  observed_at=100.0, max_age_seconds=30.0, provenance_ref="obs-1")
    values.update(changes)
    return WorldFact(**values)


def test_read_is_three_valued_and_never_returns_a_stale_value():
    world = WorldStateProjection()
    assert world.read(SCOPE, "build_green", now=100.0).status == "MISSING"
    assert world.read(SCOPE, "build_green", now=100.0).value_or_unknown() is UNKNOWN
    world.ingest(fact(), now=100.0)
    read = world.read(SCOPE, "build_green", now=110.0)
    assert isinstance(read, FactRead) and read.status == "FRESH" and read.known
    assert read.value_or_unknown() is True
    stale = world.read(SCOPE, "build_green", now=200.0)
    assert stale.status == "STALE" and stale.fact is None
    assert stale.value_or_unknown() is UNKNOWN and stale.known is False


def test_unknown_sentinel_refuses_truth_testing():
    with pytest.raises(TypeError):
        bool(UNKNOWN)


def test_future_facts_are_refused():
    world = WorldStateProjection()
    with pytest.raises(WorldStateError):
        world.ingest(fact(observed_at=500.0), now=100.0)


def test_out_of_order_ingest_does_not_regress_world_state():
    world = WorldStateProjection()
    assert world.ingest(fact(observed_at=100.0, value=True), now=100.0) is True
    assert world.ingest(fact(observed_at=50.0, value=False), now=100.0) is False
    assert world.read(SCOPE, "build_green", now=110.0).value_or_unknown() is True
    # A same-instant conflicting reading also loses: ingest order never decides.
    assert world.ingest(fact(observed_at=100.0, value=False), now=100.0) is False
    assert world.read(SCOPE, "build_green", now=110.0).value_or_unknown() is True
    assert world.ingest(fact(observed_at=105.0, value=False), now=110.0) is True
    assert world.read(SCOPE, "build_green", now=110.0).value_or_unknown() is False


def test_scopes_are_isolated():
    world = WorldStateProjection()
    world.ingest(fact(scope_id="scope-a"), now=100.0)
    assert world.read("scope-a", "build_green", now=100.0).known
    assert world.read("scope-b", "build_green", now=100.0).status == "MISSING"
    assert world.keys("scope-b") == ()


def test_scope_storage_is_bounded_with_oldest_first_eviction():
    world = WorldStateProjection(max_facts_per_scope=2)
    for index in range(5):
        world.ingest(fact(key=f"k{index}", observed_at=100.0 + index,
                          max_age_seconds=1000.0), now=200.0)
    assert world.fact_count(SCOPE) == 2
    assert world.keys(SCOPE) == ("k3", "k4")


def test_fact_from_observation_binds_scope_and_refuses_unmeasured_keys(tmp_path):
    spec = ObjectiveSpec.from_dict(raw_spec())
    target = tmp_path / "report.txt"
    target.write_text("alpha", encoding="utf-8")
    observation = FileStateObserver(enrollment(spec), target).observe(now=100.0)
    record = observation.record()
    projected = fact_from_observation(record, "exists", scope_id=SCOPE, max_age_seconds=30.0,
                                      provenance_ref=observation.provenance_digest)
    world = WorldStateProjection()
    assert world.ingest(projected, now=100.0) is True
    assert world.read(SCOPE, "exists", now=110.0).value_or_unknown() is True
    with pytest.raises(WorldStateError):
        fact_from_observation(record, "unmeasured", scope_id=SCOPE, max_age_seconds=30.0,
                              provenance_ref="obs")
    with pytest.raises(WorldStateError):
        fact_from_observation(record, "exists", scope_id="other", max_age_seconds=30.0,
                              provenance_ref="obs")
