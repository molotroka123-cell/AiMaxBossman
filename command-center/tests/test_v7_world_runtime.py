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

import asyncio
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


# ------------------------------------------------- one pass, many callers

async def test_concurrent_callers_share_one_observation_pass(svc, monkeypatch):
    """The charter's generation-aware single-flight rule, on the case this run
    created: the 60s tick and an HTTP request arriving together used to shell
    out to git twice and sample the host twice to answer one question."""
    passes = {"n": 0}
    real = observers.observe_all

    async def counted(service, *, repo=None):
        passes["n"] += 1
        await asyncio.sleep(0.05)
        return await real(service, repo=repo)

    monkeypatch.setattr(observers, "observe_all", counted)
    results = await asyncio.gather(*(world.refresh(svc) for _ in range(5)))
    assert passes["n"] == 1, f"{passes['n']} passes for 5 concurrent callers"
    assert sum(1 for r in results if r["shared"]) == 4
    assert len({r["started_at"] for r in results}) == 1


async def test_a_joined_caller_is_told_the_reading_is_not_its_own(svc, monkeypatch):
    """A shared reading is as old as the pass, not as old as the request that
    received it. Saying so is the difference between sharing work and lying
    about freshness."""
    real = observers.observe_all

    async def slow(service, *, repo=None):
        await asyncio.sleep(0.05)
        return await real(service, repo=repo)

    monkeypatch.setattr(observers, "observe_all", slow)
    first, second = await asyncio.gather(world.refresh(svc), world.refresh(svc))
    assert first["shared"] is False and second["shared"] is True
    assert second["started_at"] == first["started_at"]


async def test_a_finished_pass_is_never_reused(svc, monkeypatch):
    """Single-flight shares work in progress. Sharing a completed pass would be
    a cache, and a cache is how a stale reading gets served as a fresh one."""
    passes = {"n": 0}
    real = observers.observe_all

    async def counted(service, *, repo=None):
        passes["n"] += 1
        return await real(service, repo=repo)

    monkeypatch.setattr(observers, "observe_all", counted)
    await world.refresh(svc)
    await world.refresh(svc)
    assert passes["n"] == 2
    assert (await world.refresh(svc))["shared"] is False


async def test_a_caller_going_away_does_not_cancel_the_pass_others_await(svc, monkeypatch):
    """A cancelled request or a stopped tick must not take down the pass the
    remaining callers are waiting on."""
    real = observers.observe_all

    async def slow(service, *, repo=None):
        await asyncio.sleep(0.1)
        return await real(service, repo=repo)

    monkeypatch.setattr(observers, "observe_all", slow)
    leaving = asyncio.ensure_future(world.refresh(svc))
    await asyncio.sleep(0.01)
    staying = asyncio.ensure_future(world.refresh(svc))
    await asyncio.sleep(0.01)
    leaving.cancel()
    result = await staying
    assert result["available"] >= 1 and result["shared"] is True


async def test_a_failed_pass_is_not_remembered_as_a_failure(svc, monkeypatch):
    """The next caller starts a fresh pass. Caching the exception would turn one
    bad git invocation into a permanently blind projection."""
    calls = {"n": 0}
    real = observers.observe_all

    async def flaky(service, *, repo=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("git went away")
        return await real(service, repo=repo)

    monkeypatch.setattr(observers, "observe_all", flaky)
    with pytest.raises(RuntimeError):
        await world.refresh(svc)
    assert (await world.refresh(svc))["available"] >= 1


async def test_different_repos_are_different_flights(svc, monkeypatch):
    """A key that ignored the repo would hand one repository's worktree reading
    to a caller asking about another."""
    seen = []
    real = observers.observe_all

    async def recorded(service, *, repo=None):
        seen.append(repo)
        await asyncio.sleep(0.05)
        return await real(service, repo=repo)

    monkeypatch.setattr(observers, "observe_all", recorded)
    await asyncio.gather(world.refresh(svc, repo="a"), world.refresh(svc, repo="b"))
    assert sorted(x for x in seen if x) == ["a", "b"]


# ------------------------------------------------------ the owner's surface

@pytest.fixture
async def client(tmp_path):
    """A real client against the real router, on its own database."""
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "http",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'http' / 'w.db'}",
                        ui_dir=tmp_path / "no-ui-http")
    app = create_app(settings, announce_token=False, start_workers=False)
    service = app.state.svc
    await service.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://w",
                                     headers={HEADER: service.auth.token}) as http:
            yield http
    finally:
        await service.stop()


async def test_the_endpoint_answers_what_do_you_believe_right_now(client):
    """The charter's first UX question, over real HTTP through the real route."""
    body = (await client.get("/api/reality/world")).json()
    assert body["scope_id"] == world.AMBIENT_SCOPE
    assert body["fresh"] >= 1 and body["last_pass"]["ingested"] >= 1
    assert body["contested"] == []
    for row in body["facts"]:
        assert (row["status"] == "FRESH") == ("value" in row), row


async def test_inspecting_without_observing_is_possible(client):
    """`refresh=false` is how a fact is watched ageing out, instead of being
    renewed by the very request that came to check on it."""
    first = (await client.get("/api/reality/world")).json()
    again = (await client.get("/api/reality/world?refresh=false")).json()
    assert "last_pass" not in again
    assert {f["key"] for f in again["facts"]} == {f["key"] for f in first["facts"]}


async def test_the_endpoint_refuses_to_be_written_to(client):
    """A world state anyone can post to is a belief store, not evidence."""
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await client.request(method, "/api/reality/world",
                                        json={"key": "build_green", "value": True})
        assert response.status_code in (404, 405), (method, response.status_code)
