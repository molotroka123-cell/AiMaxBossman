"""Disagreement between observers is a finding, not a race won by recency.

The V7 multi-model audit corpus names one failure above the others for this
layer: "World State Graph becomes a stale cache treated as truth". The clearest
way to become one is to let two observers report different values for the same
key and hand the caller whichever arrived last — the read looks confident, the
disagreement is gone, and nobody can tell it happened.

So the projection keeps facts per source and reports CONTESTED while fresh
observers disagree. This is strictly a tightening: a read that used to be FRESH
can now be CONTESTED, and nothing that used to be UNKNOWN becomes known. These
tests are mostly about that direction holding.
"""
from __future__ import annotations

import pytest

from bossman_shared.objective_world_state import (
    UNKNOWN,
    WorldFact,
    WorldStateError,
    WorldStateProjection,
    require_fresh,
)

SCOPE = "mission-1"


def fact(**changes):
    values = dict(key="build_green", value=True, source_ref="ci", scope_id=SCOPE,
                  observed_at=100.0, max_age_seconds=30.0, provenance_ref="obs-1")
    values.update(changes)
    return WorldFact(**values)


# ------------------------------------------------------------ disagreement

def test_two_observers_that_disagree_leave_the_key_contested():
    world = WorldStateProjection()
    world.ingest(fact(source_ref="ci", value=True, observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="local-run", value=False, observed_at=101.0), now=101.0)
    read = world.read(SCOPE, "build_green", now=105.0)
    assert read.status == "CONTESTED"
    assert read.known is False
    assert read.value_or_unknown() is UNKNOWN


def test_the_contested_read_names_who_measured_what():
    """A summary of a disagreement is not the disagreement. The owner has to be
    able to ask which observer to trust, which needs both readings."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="ci", value=True, observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="local-run", value=False, observed_at=101.0), now=101.0)
    read = world.read(SCOPE, "build_green", now=105.0)
    assert {f.source_ref: f.value for f in read.conflicting} == {"ci": True, "local-run": False}
    assert read.sources() == ("local-run", "ci")


def test_recency_does_not_settle_a_disagreement():
    """The regression this exists to prevent: newest-wins made the projection
    confident about a fact its own observers did not agree on."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="a", value="green", observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="b", value="red", observed_at=120.0), now=120.0)
    assert world.read(SCOPE, "build_green", now=125.0).value_or_unknown() is UNKNOWN


def test_agreeing_observers_are_fresh_not_contested():
    """Corroboration must not be mistaken for conflict, or every multi-source
    key would read UNKNOWN and the projection would be useless."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="ci", value=True, observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="local-run", value=True, observed_at=101.0), now=101.0)
    read = world.read(SCOPE, "build_green", now=105.0)
    assert read.status == "FRESH" and read.value_or_unknown() is True
    assert read.fact.source_ref == "local-run", "the newest agreeing reading represents the key"


def test_a_boolean_and_an_integer_are_not_the_same_measurement():
    """Python calls True == 1. Two observers reporting those reported different
    things, and inventing agreement is the same failure as inventing freshness."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="a", value=True, observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="b", value=1, observed_at=101.0), now=101.0)
    assert world.read(SCOPE, "build_green", now=105.0).status == "CONTESTED"


def test_a_disagreement_resolves_once_the_loser_goes_stale():
    """Contested is a statement about the present, not a permanent mark: when
    only one observer is still fresh there is nothing left to disagree about."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="a", value=True, observed_at=100.0, max_age_seconds=15.0),
                 now=100.0)
    world.ingest(fact(source_ref="b", value=False, observed_at=110.0, max_age_seconds=60.0),
                 now=110.0)
    # Both fresh at 112: "a" holds until 115, "b" until 170.
    assert world.read(SCOPE, "build_green", now=112.0).status == "CONTESTED"
    # Past 115 only "b" is still fresh, so there is nothing left to disagree about.
    assert world.read(SCOPE, "build_green", now=130.0).value_or_unknown() is False


def test_an_observer_that_corrects_itself_is_not_a_disagreement():
    """One source revising its own reading is exactly the monotonic case that
    already worked; per-source storage must not turn it into a conflict."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="ci", value=True, observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="ci", value=False, observed_at=105.0), now=105.0)
    read = world.read(SCOPE, "build_green", now=110.0)
    assert read.status == "FRESH" and read.value_or_unknown() is False
    assert world.observation_count(SCOPE) == 1


def test_a_source_still_cannot_regress_its_own_reading():
    world = WorldStateProjection()
    assert world.ingest(fact(source_ref="ci", value=True, observed_at=100.0), now=100.0) is True
    assert world.ingest(fact(source_ref="ci", value=False, observed_at=50.0), now=100.0) is False
    assert world.read(SCOPE, "build_green", now=110.0).value_or_unknown() is True


# --------------------------------------------------------- nothing loosened

def test_missing_and_stale_are_unchanged():
    world = WorldStateProjection()
    assert world.read(SCOPE, "build_green", now=100.0).status == "MISSING"
    world.ingest(fact(), now=100.0)
    assert world.read(SCOPE, "build_green", now=105.0).status == "FRESH"
    assert world.read(SCOPE, "build_green", now=200.0).status == "STALE"
    assert world.read(SCOPE, "build_green", now=200.0).value_or_unknown() is UNKNOWN


def test_scopes_remain_hard_partitions_per_source():
    world = WorldStateProjection()
    world.ingest(fact(scope_id="scope-a", source_ref="a", value=True), now=100.0)
    world.ingest(fact(scope_id="scope-b", source_ref="b", value=False), now=100.0)
    assert world.read("scope-a", "build_green", now=105.0).value_or_unknown() is True
    assert world.read("scope-b", "build_green", now=105.0).value_or_unknown() is False


def test_a_future_observation_is_still_refused():
    world = WorldStateProjection()
    with pytest.raises(WorldStateError):
        world.ingest(fact(observed_at=500.0), now=100.0)


# ------------------------------------------------------------- bounded cost

def test_a_flood_of_observers_costs_a_fixed_amount():
    """Visible disagreement must not become an unbounded memory channel for a
    misbehaving source generator."""
    world = WorldStateProjection(max_sources_per_key=3)
    for i in range(20):
        world.ingest(fact(source_ref=f"src-{i:02d}", observed_at=100.0 + i), now=200.0)
    assert world.observation_count(SCOPE) == 3
    assert world.sources(SCOPE, "build_green") == ("src-17", "src-18", "src-19")


def test_key_eviction_still_bounds_a_scope():
    world = WorldStateProjection(max_facts_per_scope=2)
    for i in range(5):
        world.ingest(fact(key=f"k{i}", observed_at=100.0 + i), now=200.0)
    assert world.fact_count(SCOPE) == 2
    assert world.keys(SCOPE) == ("k3", "k4")


def test_a_key_counts_once_however_many_observers_reported_it():
    world = WorldStateProjection()
    for src in ("a", "b", "c"):
        world.ingest(fact(source_ref=src, observed_at=100.0), now=100.0)
    assert world.fact_count(SCOPE) == 1 and world.observation_count(SCOPE) == 3


# ------------------------------------------------- the effect-boundary rule

def test_require_fresh_returns_the_value_when_it_is_actually_fresh():
    world = WorldStateProjection()
    world.ingest(fact(value="abc123"), now=100.0)
    assert require_fresh(world, SCOPE, "build_green", now=110.0) == "abc123"


@pytest.mark.parametrize("now,expected", [(200.0, "STALE"), (100.0, "MISSING")])
def test_require_fresh_refuses_anything_but_fresh(now, expected):
    """The corpus rule: a stale fact may support planning, never an effect
    obligation. Callable rather than conventional, so it can be tested."""
    world = WorldStateProjection()
    if expected == "STALE":
        world.ingest(fact(), now=100.0)
    with pytest.raises(WorldStateError) as exc:
        require_fresh(world, SCOPE, "build_green", now=now)
    assert expected in str(exc.value)


def test_require_fresh_refuses_a_contested_fact_and_names_the_sources():
    """The most dangerous case: two observers disagree and the caller is about
    to act. The refusal has to say what the disagreement was."""
    world = WorldStateProjection()
    world.ingest(fact(source_ref="ci", value=True, observed_at=100.0), now=100.0)
    world.ingest(fact(source_ref="local", value=False, observed_at=101.0), now=101.0)
    with pytest.raises(WorldStateError) as exc:
        require_fresh(world, SCOPE, "build_green", now=105.0)
    message = str(exc.value)
    assert "CONTESTED" in message and "ci=True" in message and "local=False" in message


def test_the_contested_shortlist_is_what_the_owner_reads():
    world = WorldStateProjection()
    world.ingest(fact(key="build_green", source_ref="a", value=True), now=100.0)
    world.ingest(fact(key="build_green", source_ref="b", value=False), now=100.5)
    world.ingest(fact(key="head_sha", source_ref="a", value="abc"), now=100.0)
    world.ingest(fact(key="head_sha", source_ref="b", value="abc"), now=100.5)
    assert world.contested(SCOPE, now=105.0) == ("build_green",)
