"""The World State Graph has to actually exist at runtime.

Before this, `observers.py` could measure and `objective_world_state` could
store, and nothing joined them: each observation was computed for one HTTP
response and discarded. A graph that is empty between requests has no freshness
to report — every read is instantaneous by construction — and the owner cannot
ask the question the V7 charter puts first: what does Bossman believe right
now, and how sure is that?

These tests check the join, and then check the three things it must not become:
a write surface, an authorization, or a place where two observers disagreeing
turns into one confident answer.
"""
from __future__ import annotations

import time

import pytest

from bcc.reality import observers, world
from bossman_shared.objective_world_state import WorldFact, WorldStateError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def svc(tmp_path):
    from bcc.api import create_app
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "data",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data' / 'w.db'}",
                        ui_dir=tmp_path / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    service = app.state.svc
    await service.start()
    try:
        yield service
    finally:
        await service.stop()


# ------------------------------------------------------------ it is joined

async def test_a_refresh_pass_actually_populates_the_graph(svc):
    result = await world.refresh(svc)
    assert result["available"] >= 1, result
    assert result["ingested"] == result["available"]
    assert world.projection(svc).fact_count(world.AMBIENT_SCOPE) == result["available"]


async def test_what_was_observed_can_be_read_back_fresh(svc):
    await world.refresh(svc)
    body = world.belief(svc)
    fresh = [f for f in body["facts"] if f["status"] == "FRESH"]
    assert fresh, body
    for row in fresh:
        assert "value" in row and row["provenance"].startswith("bcc.reality.observers.")
        assert row["expires_at"] > row["observed_at"], "a fact with no expiry is fresh forever"


async def test_a_belief_survives_between_passes(svc):
    """The point of the graph: the second request does not start from nothing."""
    await world.refresh(svc)
    first = {f["key"] for f in world.belief(svc)["facts"]}
    assert first
    assert {f["key"] for f in world.belief(svc)["facts"]} == first


async def test_an_unavailable_adapter_contributes_nothing(svc, monkeypatch):
    """"We could not look" must not overwrite the last thing anyone measured."""
    await world.refresh(svc)
    before = world.belief(svc)
    monkeypatch.setattr(observers, "observe_git",
                        lambda repo: observers._unavailable("git", "git.worktree", "no git"))
    result = await world.refresh(svc)
    assert any(u["key"] == "git.worktree" for u in result["unavailable"])
    after = {f["key"]: f for f in world.belief(svc)["facts"]}
    kept = {f["key"]: f for f in before["facts"]}.get("git.worktree")
    if kept is not None:
        assert after["git.worktree"]["status"] in ("FRESH", "STALE")
        assert after["git.worktree"].get("value", kept.get("value")) == kept.get("value")


# ------------------------------------------------------ it does not lie fresh

async def test_a_fact_goes_stale_on_its_own_schedule(svc):
    """A value that stops being renewed must stop being reported as a value."""
    await world.refresh(svc)
    later = time.time() + 10_000
    body = world.belief(svc, now=later)
    assert body["fresh"] == 0 and body["stale"] == len(body["facts"])
    assert all("value" not in f for f in body["facts"]), "a stale row must carry no value"


async def test_two_observers_that_disagree_are_reported_as_contested(svc):
    """The failure the audit corpus names first for this layer: the graph
    becomes a cache treated as truth by resolving disagreement with recency."""
    await world.refresh(svc)
    projection = world.projection(svc)
    now = time.time()
    projection.ingest(WorldFact(key="app.inventory", value={"count": 999},
                                source_ref="observer:second-opinion",
                                scope_id=world.AMBIENT_SCOPE, observed_at=now,
                                max_age_seconds=60.0, provenance_ref="test"), now=now)
    body = world.belief(svc, now=now)
    row = next(f for f in body["facts"] if f["key"] == "app.inventory")
    assert row["status"] == "CONTESTED" and "value" not in row
    assert body["contested"] == ["app.inventory"]
    assert {c["source"] for c in row["conflicting"]} == {"observer:app", "observer:second-opinion"}


async def test_the_newer_observer_does_not_silently_win(svc):
    await world.refresh(svc)
    projection = world.projection(svc)
    now = time.time() + 1
    projection.ingest(WorldFact(key="app.inventory", value={"count": 999},
                                source_ref="observer:second-opinion",
                                scope_id=world.AMBIENT_SCOPE, observed_at=now,
                                max_age_seconds=60.0, provenance_ref="test"), now=now)
    with pytest.raises(WorldStateError) as exc:
        world.require_fresh(svc, "app.inventory", now=now)
    assert "CONTESTED" in str(exc.value)


# ------------------------------------------------ the effect-boundary rule

async def test_require_fresh_returns_a_measured_value(svc):
    await world.refresh(svc)
    key = next(f["key"] for f in world.belief(svc)["facts"] if f["status"] == "FRESH")
    assert world.require_fresh(svc, key) is not None


async def test_require_fresh_refuses_a_stale_fact(svc):
    """The corpus rule: a stale fact may support planning, never an effect."""
    await world.refresh(svc)
    key = next(f["key"] for f in world.belief(svc)["facts"] if f["status"] == "FRESH")
    with pytest.raises(WorldStateError) as exc:
        world.require_fresh(svc, key, now=time.time() + 10_000)
    assert "STALE" in str(exc.value)


async def test_require_fresh_refuses_a_key_nobody_measured(svc):
    await world.refresh(svc)
    with pytest.raises(WorldStateError) as exc:
        world.require_fresh(svc, "gpu.vram_free")
    assert "MISSING" in str(exc.value)


# --------------------------------------------------- it grants nothing

async def test_there_is_no_route_that_writes_a_fact(svc):
    """A world state anyone can post to is a belief store, not evidence."""
    from bcc.features import reality as feature
    writes = [r for r in feature.router.routes
              if set(getattr(r, "methods", ())) & {"POST", "PUT", "PATCH", "DELETE"}
              and "/reality/world" in getattr(r, "path", "")]
    assert writes == []


async def test_the_ambient_scope_does_not_share_a_namespace_with_missions(svc):
    """Scopes are hard partitions; ambient system facts must not be readable as
    a mission's evidence."""
    await world.refresh(svc)
    projection = world.projection(svc)
    assert projection.fact_count("mission-1") == 0
    assert projection.keys("mission-1") == ()


async def test_a_clock_skewed_reading_degrades_instead_of_failing_the_pass(svc, monkeypatch):
    """An adapter that timestamps into the future would otherwise be refused by
    the projection and take the whole refresh down with it."""
    real = observers.observe_process

    def skewed():
        obs = real()
        obs.observed_at = time.time() + 3600
        return obs

    monkeypatch.setattr(observers, "observe_process", skewed)
    result = await world.refresh(svc)
    assert result["ingested"] >= 1
    row = next(f for f in world.belief(svc)["facts"] if f["key"] == "process.host")
    assert row["status"] == "FRESH"


# ------------------------------------------------------------ the tick

async def test_the_refresh_tick_is_registered_and_slower_than_the_facts_it_holds(svc):
    """A refresh that always beat every expiry would mean the freshness
    machinery never reports anything."""
    from bcc.features import reality as feature
    assert feature.FEATURE.tick is not None and feature.FEATURE.tick_seconds > 0
    assert feature.TICK_SECONDS > min(observers.VALIDITY.values())
    assert "reality" in svc.feature_ticks
