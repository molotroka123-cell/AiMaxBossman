"""Freeze regression: SATISFIED is an evidence boundary, never a string check."""
from __future__ import annotations

import pytest

from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore, ObjectiveStoreError


def _spec() -> ObjectiveSpec:
    return ObjectiveSpec.from_dict({
        "schema_version": 1,
        "owner_id": "owner",
        "scope_id": "project",
        "objective_id": "build",
        "revision": 1,
        "previous_digest": None,
        "sources": [{
            "source_ref": "local-build",
            "source_revision": "v1",
            "max_age_seconds": 30,
        }],
        "predicates": [{
            "predicate_id": "green",
            "source_ref": "local-build",
            "field": "passed",
            "value_type": "boolean",
            "operator": "eq",
            "expected": True,
        }],
        "expires_at": 1000,
        "priority": 2,
        "allowed_triggers": ["source_change"],
        "permission_refs": ["owner-grant"],
        "conflict_keys": ["project-build"],
        "cooldown_seconds": 60,
        "limits": {
            "max_observations": 8,
            "max_missions": 2,
            "max_wall_seconds": 300,
            "max_cost_usd": 5,
        },
        "stop_conditions": ["owner-revocation", "budget-exhausted"],
    })


def test_arbitrary_nonempty_string_cannot_purchase_satisfied(tmp_path):
    spec = _spec()
    store = ObjectiveStore(tmp_path / "v5.db")
    state = store.create(spec)
    state = store.transition(
        "build", "ACTIVE", now=100, owner_id="owner", expected_version=state.version,
    )

    with pytest.raises(ObjectiveStoreError):
        store.set_condition(
            "build", "SATISFIED", evidence_ref="x", expected_version=state.version,
        )

    after = store.get("build")
    assert after.condition == "UNKNOWN"
    assert after.last_verified_evidence_ref is None
    assert after.version == state.version
