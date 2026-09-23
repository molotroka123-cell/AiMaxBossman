"""Aster6 media findings (audit f09c6065, tests/aster6_repro_media.py) as regressions.

1. A decodable engine output that is far SHORTER (or longer) than the request is not the clip
   that was asked for: the job must fail (malformed) and no run may be published. Before the fix
   a 10 s Wan request whose output decoded to ~1.94 s ended completed / PASS.
2. An explicit provider.hard_timeout_s wins exactly, even when it happens to equal the catalog
   default (3660.0 for Wan). Before the fix it was mistaken for "not set" and the scaled catalog
   budget (33629.86 s) won.
3. catalog.validate_settings rejects insane work before any scaling.

Every engine here is a MOCK_ENGINE (fixtures from test_studio_sdcpp_provider /
test_studio_sdcpp_hostile); nothing is live evidence.
"""
from __future__ import annotations

import pytest

from bcc.features.images import process_one
from bcc.studio import catalog
from bcc.studio.providers import sdcpp

from .test_studio_sdcpp_hostile import harness, plane  # noqa: F401
from .test_studio_sdcpp_provider import _job, engine  # noqa: F401

WAN = "sdcpp:wan2.2-ti2v-5b"


async def _outcome(env, jid):
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    runs = [r for r in (await env.client.get("/api/studio/runs")).json()["items"] if r.get("job_id") in (None, jid)]
    return job, runs


@pytest.mark.parametrize("length", ["10s", "5s"])
async def test_short_decodable_output_does_not_complete(env, engine, length):
    engine["value"] = "short"                          # 1 s (16 frames) whatever was asked
    jid = await _job(env, WAN, length=length, width=640, height=352, steps=16, seed=7)
    await process_one(env.svc)
    job, runs = await _outcome(env, jid)
    assert job["status"] == "failed", "a wrong-length output was accepted as completed"
    assert job["studio"]["reason"] == "malformed", job["studio"]
    assert not runs, "an output of the wrong length was published as a run"


async def test_too_long_output_does_not_complete(env, engine):
    engine["value"] = "long"                           # twice the requested frames
    jid = await _job(env, WAN, length="5s", width=640, height=352, steps=16, seed=7)
    await process_one(env.svc)
    job, runs = await _outcome(env, jid)
    assert job["status"] == "failed" and job["studio"]["reason"] == "malformed", job
    assert not runs


async def test_output_of_the_requested_length_completes(env, engine):
    """Negative control: an engine that renders what was asked still completes."""
    jid = await _job(env, WAN, length="10s", width=640, height=352, steps=16, seed=7)
    await process_one(env.svc)
    job, runs = await _outcome(env, jid)
    assert job["status"] == "completed", job
    gen = runs[0]["provenance"]["settings_resolved"]["engine_trace"]["generation"]
    assert gen["duration_s_declared"] == 161 / 16
    assert abs(gen["duration_s_observed"] - 161 / 16) < 0.07


def test_duration_tolerance_is_bounded_both_ways():
    s = {"frames": 81, "fps": 16}
    assert sdcpp.duration_mismatch(81 / 16 * 1000, s) is None
    assert sdcpp.duration_mismatch(80 / 16 * 1000, s) is None     # one frame short: container rounding
    assert sdcpp.duration_mismatch(16 / 16 * 1000, s)             # the Aster6 case, per segment
    assert sdcpp.duration_mismatch(162 / 16 * 1000, s)            # double length
    assert sdcpp.duration_mismatch(None, s)                       # a video without a duration
    assert sdcpp.duration_mismatch(1000, {}) is None              # nothing requested: nothing to hold to


@pytest.mark.parametrize("override", [60.0, 3660.0])
async def test_explicit_timeout_override_wins_even_when_equal_to_catalog(harness, override):
    provider = harness.provider(WAN)
    assert provider._catalog_timeout_s == 3660.0       # the collision the audit found
    provider.hard_timeout_s = override
    receipt = await provider.submit(plane(WAN, width=1280, height=704, frames=81, steps=50))
    job = provider.job_record(receipt.request_id)
    try:
        assert job["hard_timeout_s"] == override
    finally:
        await provider.cancel(receipt.request_id)      # before the task gets a turn: no launch


async def test_without_override_the_scaled_budget_applies(harness):
    """Negative control: no owner number -> the work-proportional budget, not the catalog value."""
    provider = harness.provider(WAN)
    settings = {"width": 1280, "height": 704, "frames": 81, "steps": 50}
    receipt = await provider.submit(plane(WAN, **settings))
    job = provider.job_record(receipt.request_id)
    try:
        expected = sdcpp.segment_deadline_s(provider.model, {**plane(WAN, **settings).settings})
        assert job["hard_timeout_s"] == expected != 3660.0
    finally:
        await provider.cancel(receipt.request_id)


@pytest.mark.parametrize("field,value", [
    ("width", 10**12), ("height", 10**12), ("frames", 10**12),
    ("steps", 10**12), ("length", "100000s"), ("segments", 100000),
])
def test_catalog_rejects_insane_work_before_scaling(field, value):
    model = next(m for m in catalog.load()["models"] if m["id"] == WAN)
    with pytest.raises(ValueError):
        catalog.validate_settings(model, {field: value})
