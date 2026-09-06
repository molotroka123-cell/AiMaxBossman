"""Pure V5 foundation tests; no observer, migration or runtime acceptance claim."""
import copy
import hashlib
from dataclasses import FrozenInstanceError
import json

import pytest

from bossman_shared.objective_spec import ObjectiveSpec, ObjectiveValidationError, evaluate


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
        "limits": {"max_observations": 3, "max_missions": 2, "max_wall_seconds": 300, "max_cost_usd": 0},
        "stop_conditions": ["owner-revocation", "budget-exhausted"],
    }


def observation(spec, **changes):
    value = {"observation_id": "obs-1", "owner_id": "owner", "scope_id": "project",
             "objective_digest": spec.digest, "source_ref": "local-build", "source_revision": "v1",
             "observed_at": 90, "values": {"passed": True}}
    value.update(changes)
    return value


def run(spec, observations, **changes):
    options = dict(now=100, lifecycle="ACTIVE", enrolled_sources=("local-build",), trigger="source_change")
    options.update(changes)
    return evaluate(spec, observations, **options)


def test_roundtrip_digest_deep_immutability():
    raw = raw_spec()
    spec = ObjectiveSpec.from_dict(raw)
    digest = spec.digest
    raw["limits"]["max_missions"] = 999
    exported = spec.to_dict()
    exported["sources"].clear()
    assert spec.digest == digest
    assert ObjectiveSpec.from_json(spec.to_json()) == spec
    assert ObjectiveSpec.from_dict(json.loads(json.dumps(raw_spec(), sort_keys=True))).digest == digest
    with pytest.raises(FrozenInstanceError):
        spec._json = "{}"
    with pytest.raises(TypeError):
        ObjectiveSpec()


def test_trusted_revision_required_identity_immutable_and_old_observation_unknown():
    first = ObjectiveSpec.from_dict(raw_spec())
    raw = first.to_dict()
    raw.update(revision=2, previous_digest=first.digest)
    raw["priority"] = 1
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_dict(raw)
    second = ObjectiveSpec.from_dict(raw, previous=first)
    assert second.digest != first.digest
    assert ObjectiveSpec.from_json(second.to_json(), previous=first) == second
    assert run(second, [observation(first)]).condition == "UNKNOWN"
    for key, value in (("owner_id", "another"), ("scope_id", "another"),
                       ("objective_id", "another"), ("previous_digest", "bad"), ("revision", 3)):
        changed = copy.deepcopy(raw)
        changed[key] = value
        with pytest.raises(ObjectiveValidationError):
            ObjectiveSpec.from_dict(changed, previous=first)


@pytest.mark.parametrize("path", ["schema_version", "revision", "expires_at", "priority", "cooldown_seconds",
                                  "limits.max_observations", "limits.max_missions", "limits.max_wall_seconds", "limits.max_cost_usd"])
@pytest.mark.parametrize("value", [True, "1", -1, float("nan"), float("inf"), 10 ** 400])
def test_strict_numeric_validation(path, value):
    raw = raw_spec()
    names = path.split(".")
    target = raw if len(names) == 1 else raw[names[0]]
    target[names[-1]] = value
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_dict(raw)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(extra=True),
    lambda r: r.update(sources=[]),
    lambda r: r.update(predicates=[]),
    lambda r: r["sources"].append(r["sources"][0].copy()),
    lambda r: r["predicates"].append(r["predicates"][0].copy()),
    lambda r: r["predicates"][0].update(source_ref="outside"),
    lambda r: r["predicates"][0].update(operator="ge"),
    lambda r: r["predicates"][0].update(expected=1),
    lambda r: r.update(permission_refs=[]),
    lambda r: r.update(allowed_triggers=["everything"]),
    lambda r: r["sources"][0].update(max_age_seconds=86401),
    lambda r: r.update(owner_id="\ud800"),
    lambda r: r.update(conflict_keys=("not-json",)),
])
def test_invalid_contracts(mutation):
    raw = raw_spec()
    mutation(raw)
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_dict(raw)


@pytest.mark.parametrize("value", ['{"schema_version":1,"schema_version":1}', '{"value":NaN}', '[]', '"text"'])
def test_json_boundary(value):
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_json(value)


@pytest.mark.parametrize("change,reason", [
    ({"observed_at": 69}, "stale"), ({"observed_at": 101}, "invalid_observation_time"),
    ({"observed_at": True}, "invalid_observation_time"),
    ({"observed_at": -1}, "invalid_observation_time"),
    ({"source_revision": "v2"}, "source_revision_mismatch"),
    ({"owner_id": "other"}, "identity_mismatch"),
    ({"scope_id": "other"}, "identity_mismatch"),
    ({"objective_digest": "other"}, "identity_mismatch"),
    ({"values": {}}, "missing_value"), ({"values": {"passed": 1}}, "invalid_value_type"),
    ({"observation_id": ""}, "malformed_observation"),
])
def test_observation_matrix(change, reason):
    spec = ObjectiveSpec.from_dict(raw_spec())
    result = run(spec, [observation(spec, **change)])
    assert result.condition == "UNKNOWN"
    assert result.predicate_results[0][2] == reason
    assert not result.proposal_candidate
    assert not result.admission_allowed


def test_missing_duplicate_changed_source_and_no_implicit_enrollment():
    spec = ObjectiveSpec.from_dict(raw_spec())
    obs = observation(spec)
    for batch in ([], [obs, obs], [observation(spec, source_ref="outside")]):
        assert run(spec, batch).condition == "UNKNOWN"
    default = evaluate(spec, [obs], now=100)
    assert default.lifecycle == "DRAFT"
    assert default.condition == "UNKNOWN"
    assert default.predicate_results[0][2] == "not_enrolled"
    assert not default.admission_allowed


def test_freshness_boundary_and_drift_candidate_is_never_admission():
    spec = ObjectiveSpec.from_dict(raw_spec())
    assert run(spec, [observation(spec, observed_at=70)]).condition == "SATISFIED"
    result = run(spec, [observation(spec, values={"passed": False})])
    assert result.condition == "DEVIATED" and result.proposal_candidate
    assert not result.admission_allowed
    assert not run(spec, [observation(spec, values={"passed": False})], trigger="scheduled").proposal_candidate


@pytest.mark.parametrize("lifecycle", ["DRAFT", "PAUSED", "EXPIRED", "REVOKED"])
def test_nonactive_lifecycle_never_proposes_or_admits(lifecycle):
    spec = ObjectiveSpec.from_dict(raw_spec())
    result = run(spec, [observation(spec, values={"passed": False})], lifecycle=lifecycle)
    assert result.lifecycle == lifecycle
    assert not result.proposal_candidate and not result.admission_allowed


def test_expiry_inclusive_revocation_sticky_and_import_never_activates():
    spec = ObjectiveSpec.from_json(ObjectiveSpec.from_dict(raw_spec()).to_json())
    obs = observation(spec, observed_at=1000, values={"passed": False})
    assert evaluate(spec, [obs], now=100).lifecycle == "DRAFT"
    for state in ("ACTIVE", "PAUSED", "DRAFT", "EXPIRED"):
        result = run(spec, [obs], now=1000, lifecycle=state)
        assert result.lifecycle == "EXPIRED" and not result.proposal_candidate
    assert run(spec, [obs], now=1000, lifecycle="REVOKED").lifecycle == "REVOKED"


def test_quota_and_unknown_dominate_over_known_drift():
    raw = raw_spec()
    raw["predicates"].append(dict(raw["predicates"][0], predicate_id="other", field="other"))
    spec = ObjectiveSpec.from_dict(raw)
    assert run(spec, [observation(spec, values={"passed": False})]).condition == "UNKNOWN"
    raw = raw_spec()
    raw["limits"]["max_observations"] = 0
    spec = ObjectiveSpec.from_dict(raw)
    result = run(spec, [observation(spec)])
    assert result.condition == "UNKNOWN" and result.predicate_results[0][2] == "batch_quota_exceeded"


@pytest.mark.parametrize("kind,operator,expected,actual,status", [
    ("number", "ge", -2, -1, "SATISFIED"), ("number", "le", 2, 3, "DEVIATED"),
    ("number", "eq", 1, True, "UNKNOWN"), ("text", "eq", "ready", "ready", "SATISFIED"),
    ("text", "eq", "ready", False, "UNKNOWN"),
])
def test_typed_predicates(kind, operator, expected, actual, status):
    raw = raw_spec()
    raw["predicates"][0].update(value_type=kind, operator=operator, expected=expected)
    spec = ObjectiveSpec.from_dict(raw)
    assert run(spec, [observation(spec, values={"passed": actual})]).condition == status


@pytest.mark.parametrize("kwargs", [{"now": True}, {"now": float("nan")}, {"lifecycle": "BOGUS"},
                                   {"enrolled_sources": ("outside",)}, {"enrolled_sources": ["local-build"]}])
def test_bad_evaluation_inputs(kwargs):
    spec = ObjectiveSpec.from_dict(raw_spec())
    with pytest.raises(ObjectiveValidationError):
        run(spec, [], **kwargs)


from bossman_shared.objective_spec import project_proposal, transition_lifecycle


def ledger_snapshot(spec, **changes):
    value = dict(owner_id="owner", scope_id="project", objective_digest=spec.digest,
                 lifecycle="ACTIVE", enrolled_sources=["local-build"],
                 observations_used=0, missions_used=0, wall_seconds_used=0,
                 cost_usd_used=0, last_proposal_at=None, stopped=False)
    value.update(changes)
    return value


def proposal(spec, batch=None, **changes):
    return project_proposal(spec, batch if batch is not None else [observation(spec, values={"passed": False})],
                            now=100, snapshot=ledger_snapshot(spec, **changes), trigger="source_change")


@pytest.mark.parametrize("current", ["DRAFT", "ACTIVE", "PAUSED", "EXPIRED", "REVOKED"])
@pytest.mark.parametrize("requested", ["DRAFT", "ACTIVE", "PAUSED", "EXPIRED", "REVOKED"])
def test_full_lifecycle_transition_matrix(current, requested):
    spec = ObjectiveSpec.from_dict(raw_spec())
    allowed = {"DRAFT": {"DRAFT", "ACTIVE", "PAUSED", "REVOKED"},
               "ACTIVE": {"ACTIVE", "PAUSED", "REVOKED"},
               "PAUSED": {"PAUSED", "ACTIVE", "REVOKED"},
               "EXPIRED": {"EXPIRED", "REVOKED"}, "REVOKED": {"REVOKED"}}
    kwargs = dict(now=100, owner_id="owner", scope_id="project")
    if requested in allowed[current]:
        assert transition_lifecycle(spec, current, requested, **kwargs) == requested
    else:
        with pytest.raises(ObjectiveValidationError):
            transition_lifecycle(spec, current, requested, **kwargs)


def test_transition_expiry_owner_and_scope_cannot_be_bypassed():
    spec = ObjectiveSpec.from_dict(raw_spec())
    for options in (dict(now=1000, owner_id="owner", scope_id="project"),
                    dict(now=100, owner_id="intruder", scope_id="project"),
                    dict(now=100, owner_id="owner", scope_id="outside")):
        with pytest.raises(ObjectiveValidationError):
            transition_lifecycle(spec, "PAUSED", "ACTIVE", **options)
    assert transition_lifecycle(spec, "ACTIVE", "EXPIRED", now=1000,
                                owner_id="owner", scope_id="project") == "EXPIRED"
    assert transition_lifecycle(spec, "REVOKED", "REVOKED", now=1000,
                                owner_id="owner", scope_id="project") == "REVOKED"


def test_proposal_content_binding_freshness_and_immutable_projection():
    spec = ObjectiveSpec.from_dict(raw_spec())
    obs = observation(spec, values={"passed": False})
    first = proposal(spec, [obs])
    assert first is not None and not first.admission_allowed
    assert first.valid_until == 120
    assert first == proposal(spec, [obs])
    obs["values"]["irrelevant"] = "changed source content"
    assert first.proposal_id != proposal(spec, [obs]).proposal_id
    with pytest.raises(FrozenInstanceError):
        first.valid_until = 9999
    assert proposal(spec, [observation(spec, observed_at=69, values={"passed": False})]) is None
    assert proposal(spec, [observation(spec, owner_id="intruder", values={"passed": False})]) is None


def test_duplicate_triggers_and_reordered_observations_keep_same_proposal_key():
    raw = raw_spec()
    raw["allowed_triggers"].append("scheduled")
    raw["sources"].append(dict(raw["sources"][0], source_ref="second"))
    raw["predicates"].append(dict(raw["predicates"][0], source_ref="second", predicate_id="second"))
    spec = ObjectiveSpec.from_dict(raw)
    batch = [observation(spec, values={"passed": False}),
             observation(spec, source_ref="second", observation_id="obs-2", values={"passed": False})]
    state = ledger_snapshot(spec, enrolled_sources=["local-build", "second"])
    first = project_proposal(spec, batch, now=100, snapshot=state, trigger="source_change")
    second = project_proposal(spec, list(reversed(batch)), now=101, snapshot=state, trigger="scheduled")
    assert first == second
    assert first is not None
    assert project_proposal(spec, batch + [batch[0]], now=100, snapshot=state, trigger="scheduled") is None


@pytest.mark.parametrize("changes", [
    {"observations_used": 3}, {"missions_used": 2}, {"wall_seconds_used": 300},
    {"cost_usd_used": .01}, {"last_proposal_at": 41}, {"stopped": True},
    {"lifecycle": "REVOKED"}, {"lifecycle": "PAUSED"}, {"enrolled_sources": []},
])
def test_cumulative_quota_cooldown_stop_and_revocation(changes):
    assert proposal(ObjectiveSpec.from_dict(raw_spec()), **changes) is None


def test_quota_and_cooldown_boundaries_zero_cost_can_propose_local_work():
    spec = ObjectiveSpec.from_dict(raw_spec())
    assert proposal(spec, observations_used=2, missions_used=1, wall_seconds_used=299,
                    cost_usd_used=0, last_proposal_at=40) is not None
    raw = raw_spec()
    raw["limits"]["max_wall_seconds"] = 0
    assert proposal(ObjectiveSpec.from_dict(raw)) is None


@pytest.mark.parametrize("changes", [
    {"owner_id": "intruder"}, {"scope_id": "other"}, {"objective_digest": "old"},
    {"last_proposal_at": 101}, {"missions_used": True}, {"observations_used": -1},
    {"wall_seconds_used": float("inf")}, {"cost_usd_used": float("nan")},
    {"stopped": "false"}, {"extra": 1}, {"enrolled_sources": ["outside"]},
])
def test_malformed_or_wrong_revision_ledger_snapshot_rejected(changes):
    with pytest.raises(ObjectiveValidationError):
        proposal(ObjectiveSpec.from_dict(raw_spec()), **changes)


def test_revision_never_reuses_old_proposal_or_snapshot():
    first = ObjectiveSpec.from_dict(raw_spec())
    raw = first.to_dict()
    raw.update(revision=2, previous_digest=first.digest)
    second = ObjectiveSpec.from_dict(raw, previous=first)
    assert proposal(first).proposal_id != proposal(second).proposal_id
    with pytest.raises(ObjectiveValidationError):
        project_proposal(second, [observation(second)], now=100,
                         snapshot=ledger_snapshot(first), trigger="source_change")
    assert proposal(second, [observation(first, values={"passed": False})]) is None


def test_observation_identity_reuse_across_sources_cannot_propose():
    raw = raw_spec()
    raw["sources"].append(dict(raw["sources"][0], source_ref="second"))
    raw["predicates"].append(dict(raw["predicates"][0], source_ref="second", predicate_id="second"))
    spec = ObjectiveSpec.from_dict(raw)
    batch = [observation(spec, values={"passed": False}),
             observation(spec, source_ref="second", values={"passed": False})]
    result = run(spec, batch, enrolled_sources=("local-build", "second"))
    assert result.condition == "UNKNOWN"
    assert {p[2] for p in result.predicate_results} == {"ambiguous_observation_identity"}
    assert proposal(spec, batch, enrolled_sources=["local-build", "second"]) is None


@pytest.mark.parametrize("identity", [" padded", "nul\x00id"])
def test_noncanonical_observation_identity_cannot_propose(identity):
    spec = ObjectiveSpec.from_dict(raw_spec())
    assert proposal(spec, [observation(spec, observation_id=identity, values={"passed": False})]) is None


def test_no_constructor_accepts_execution_authority():
    from bossman_shared.objective_spec import Evaluation, ProposalProjection
    with pytest.raises(TypeError):
        Evaluation("ACTIVE", "DEVIATED", (), True, admission_allowed=True)
    with pytest.raises(TypeError):
        ProposalProjection("id", "digest", (), 123, admission_allowed=True)


# --------------------------------------------------- trusted rehydration


def _revision_two(base: dict) -> tuple[ObjectiveSpec, ObjectiveSpec]:
    first = ObjectiveSpec.from_dict(base)
    second = ObjectiveSpec.from_dict(
        {**base, "revision": 2, "previous_digest": first.digest}, previous=first)
    return first, second


def test_from_trusted_json_reads_a_revision_its_predecessor_is_gone():
    """The rule from_dict enforces at write time would brick a store at read time."""
    _, second = _revision_two(raw_spec())
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_json(second.to_json())
    rehydrated = ObjectiveSpec.from_trusted_json(second.to_json(), digest=second.digest)
    assert rehydrated.digest == second.digest
    assert rehydrated.to_dict() == second.to_dict()


def test_from_trusted_json_refuses_bytes_that_do_not_match_the_digest():
    """The recorded digest is what replaces the chain rule, so it must bind."""
    _, second = _revision_two(raw_spec())
    other = ObjectiveSpec.from_dict({**raw_spec(), "priority": 7})
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_trusted_json(second.to_json(), digest=other.digest)


def test_from_trusted_json_refuses_tampered_storage():
    """A store whose bytes were edited is refused, not served as canonical."""
    first = ObjectiveSpec.from_dict(raw_spec())
    tampered = first.to_json().replace('"priority":', '"priority":', 1)
    tampered = json.dumps({**json.loads(tampered), "priority": 99},
                          sort_keys=True, separators=(",", ":"))
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_trusted_json(tampered, digest=first.digest)


def test_from_trusted_json_still_refuses_structurally_invalid_storage():
    """Trust covers the chain only; a malformed record is never rehydrated."""
    broken = {**raw_spec()}
    broken.pop("predicates")
    text = json.dumps(broken, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_trusted_json(text, digest=digest)


@pytest.mark.parametrize("digest", [None, "", "zz", "A" * 64, 1, "a" * 63, "a" * 65])
def test_from_trusted_json_requires_a_recorded_lowercase_digest(digest):
    first = ObjectiveSpec.from_dict(raw_spec())
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_trusted_json(first.to_json(), digest=digest)


def test_from_trusted_json_refuses_non_text_and_non_object_records():
    first = ObjectiveSpec.from_dict(raw_spec())
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_trusted_json(b"{}", digest=first.digest)
    text = "[]"
    with pytest.raises(ObjectiveValidationError):
        ObjectiveSpec.from_trusted_json(
            text, digest=hashlib.sha256(text.encode("utf-8")).hexdigest())
