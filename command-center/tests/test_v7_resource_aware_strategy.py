"""A strategy that needs memory the machine does not have is not a strategy.

`Strategy.resource_pressure` existed as a utility term and was always 0.0:
nothing measured memory and nothing fed it in, so the router weighed latency
and money against a resource cost that was decoration. The scenario this must
handle is the plain one — a local model needs 70 GB, the machine has 42 GB free
— and the answer has to be "not offered", not "offered with a small penalty".

The harder half is the unmeasured case. Before its first sample the system once
planned against a fictional 128 GB; the lesson recorded from that is that
unknown is not zero and not plenty. So a path with a declared requirement and
no measurement is refused, and refused with the reason attached rather than
silently dropped.

Nothing here routes anything: the shadow router still only ranks.
"""
from __future__ import annotations

import pytest

from bcc.reality import strategy as st

GB = 1024.0


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def svc(tmp_path):
    from bcc.api import create_app
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "data",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data' / 'r.db'}",
                        ui_dir=tmp_path / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    service = app.state.svc
    await service.start()
    try:
        yield service
    finally:
        await service.stop()


@pytest.fixture
async def client(tmp_path):
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "http",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'http' / 'r.db'}",
                        ui_dir=tmp_path / "no-ui-http")
    app = create_app(settings, announce_token=False, start_workers=False)
    service = app.state.svc
    await service.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://r",
                                     headers={HEADER: service.auth.token}) as http:
            yield http
    finally:
        await service.stop()


def health(**kw):
    return kw


def gen(**kw):
    kw.setdefault("deterministic_available", False)
    return st.generate_strategies(**kw)


def by_id(candidates):
    return {c.strategy_id: c for c in candidates}


# --------------------------------------------------- the brief's own scenario

def test_a_model_that_does_not_fit_is_not_offered():
    """70 GB needed, 42 GB free."""
    got = by_id(gen(memory=st.MemoryReading(42 * GB, "psutil"),
                    model_memory_mb={"large_model": 70 * GB}))
    large = got["large-model-tools"]
    assert large.available is False
    assert "не помещается" in large.unavailable_reason
    # The message carries both figures in MB, so the owner can see the gap.
    assert "71680" in large.unavailable_reason and "43008" in large.unavailable_reason


def test_the_router_will_not_select_a_path_that_does_not_fit():
    candidates = gen(memory=st.MemoryReading(42 * GB, "psutil"),
                     model_memory_mb={"large_model": 70 * GB})
    decision = st.shadow_route(candidates, permissions=[])
    assert decision.selected is not None
    assert decision.selected.strategy_id != "large-model-tools"
    assert all(s.strategy_id != "large-model-tools" for s in decision.ranked)


def test_a_smaller_path_survives_the_same_reading():
    """Refusing the big model must not refuse everything: the point is to pick
    the alternative, not to give up."""
    got = by_id(gen(memory=st.MemoryReading(42 * GB, "psutil"),
                    model_memory_mb={"large_model": 70 * GB, "small_model": 8 * GB}))
    assert got["small-model-tools"].available is True
    assert got["large-model-tools"].available is False


def test_a_path_that_fits_is_charged_for_what_it_occupies():
    """Fitting is not free: memory a model holds is memory the owner's work
    cannot use, so it costs utility in proportion."""
    roomy = by_id(gen(memory=st.MemoryReading(128 * GB, "psutil"),
                      model_memory_mb={"large_model": 8 * GB}))["large-model-tools"]
    tight = by_id(gen(memory=st.MemoryReading(16 * GB, "psutil"),
                      model_memory_mb={"large_model": 8 * GB}))["large-model-tools"]
    assert 0 < roomy.resource_pressure < tight.resource_pressure
    assert roomy.utility > tight.utility


def test_headroom_is_reserved_so_the_machine_can_still_answer():
    """A model may not claim the last byte: 85% is the policy, and a path that
    fits only by taking everything is refused."""
    got = by_id(gen(memory=st.MemoryReading(100 * GB, "psutil"),
                    model_memory_mb={"large_model": 90 * GB}))
    assert got["large-model-tools"].available is False
    assert st.MEMORY_HEADROOM < 1.0


# ------------------------------------------------ unknown is not "plenty"

def test_an_unmeasured_budget_refuses_a_declared_requirement():
    """The V6 defect this mirrors: planning against memory nobody sampled."""
    got = by_id(gen(memory=None, model_memory_mb={"large_model": 70 * GB}))
    large = got["large-model-tools"]
    assert large.available is False
    assert "не измерена" in large.unavailable_reason


def test_an_explicitly_unmeasured_reading_is_refused_too():
    """`MemoryReading(None)` is the honest output of a probe that could not
    look. It must behave like no reading at all, not like zero pressure."""
    reading = st.MemoryReading(None, "psutil unavailable")
    assert reading.measured is False
    got = by_id(gen(memory=reading, model_memory_mb={"large_model": 70 * GB}))
    assert got["large-model-tools"].available is False
    assert "psutil unavailable" in got["large-model-tools"].unavailable_reason


def test_unmeasured_memory_does_not_refuse_a_path_that_asks_for_none():
    """Only declared requirements are gated. A cloud path claims no local
    memory, so an unmeasured machine says nothing about it."""
    got = by_id(gen(memory=None, model_memory_mb={"large_model": 70 * GB}))
    assert got["small-model-tools"].available is True
    assert got["human-escalation"].available is True


# ------------------------------------------------------- nothing regressed

def test_without_declared_requirements_behaviour_is_unchanged():
    """Callers that know nothing about memory get exactly what they got before:
    every path available and no resource pressure."""
    for candidate in gen():
        assert candidate.available is True
        assert candidate.resource_pressure == 0.0


def test_deterministic_path_is_never_memory_gated():
    """It runs no model, so it claims no model memory."""
    got = by_id(gen(deterministic_available=True, memory=None,
                    model_memory_mb={"large_model": 70 * GB, "small_model": 70 * GB}))
    assert got["deterministic-tool"].available is True


def test_the_refusal_travels_in_the_serialised_terms():
    """A router whose reasoning cannot be read is a router nobody can review."""
    terms = by_id(gen(memory=st.MemoryReading(42 * GB),
                      model_memory_mb={"large_model": 70 * GB}))["large-model-tools"].terms()
    assert terms["available"] is False and terms["unavailable_reason"]


def test_when_everything_is_refused_the_reason_says_which_and_why():
    """Silence would be the worst outcome: the owner needs to know the machine
    was too small, not that the router had no opinion."""
    candidates = gen(memory=st.MemoryReading(1 * GB, "psutil"),
                     model_memory_mb={"small_model": 70 * GB, "large_model": 70 * GB})
    decision = st.shadow_route([c for c in candidates if c.path != "human"], permissions=[])
    assert decision.selected is None
    assert "не помещается" in decision.reason
    assert "small-model-tools" in decision.reason and "large-model-tools" in decision.reason


def test_resource_refusal_cannot_be_outscored():
    """Like permissions: an unavailable path is filtered BEFORE ranking, so no
    utility total can bring it back."""
    fits = st.Strategy("cheap", "small_model", st.Band("high", 9, "health=healthy"),
                       goal_value=1.0)
    refused = st.Strategy("huge", "large_model", st.Band("high", 9, "health=healthy"),
                          goal_value=10_000.0, unavailable_reason="не помещается")
    decision = st.shadow_route([refused, fits], permissions=[])
    assert decision.selected is fits
    assert refused not in decision.ranked


# ------------------------------------------- the endpoint reads real memory

@pytest.mark.anyio
async def test_the_endpoint_refuses_an_oversized_model_against_measured_memory(client):
    """End to end: the observer measures the host, the projection holds it with
    a freshness window, and the router sizes a declared requirement against it."""
    await client.get("/api/reality/world")          # one observation pass
    body = (await client.get("/api/reality/strategies?large_model_mb=999999999")).json()
    assert body["memory"]["measured"] is True and body["memory"]["available_mb"] > 0
    large = next(s for s in body["ranked"] + body.get("refused", [])
                 if s["strategy_id"] == "large-model-tools") \
        if body.get("refused") else None
    assert all(s["strategy_id"] != "large-model-tools" for s in body["ranked"]), \
        "a model larger than the machine must not be ranked"


@pytest.mark.anyio
async def test_the_endpoint_reports_unmeasured_before_any_observation(client):
    """No pass yet means MISSING, and MISSING is not a memory figure."""
    body = (await client.get("/api/reality/strategies?large_model_mb=1024")).json()
    assert body["memory"]["measured"] is False
    assert "missing" in body["memory"]["source"]
    assert all(s["strategy_id"] != "large-model-tools" for s in body["ranked"])


@pytest.mark.anyio
async def test_a_stale_reading_is_not_a_memory_figure(svc):
    """Freshness is the point of reading through the projection: an old sample
    is the absence of a current one, not a smaller number."""
    import time as _time
    from bcc.features import reality as feature
    from bcc.reality import world
    await world.refresh(svc)
    fresh = feature.memory_reading(svc)
    assert fresh.measured is True
    stale = feature.memory_reading(svc, now=_time.time() + 10_000)
    assert stale.measured is False and "stale" in stale.source
