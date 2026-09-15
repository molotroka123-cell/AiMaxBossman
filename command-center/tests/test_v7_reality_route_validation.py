"""Astra/Codex F5 (2026-09-08, 2/2 on 45027d3): the HTTP route neutralised
invalid memory requirements before the generator could refuse them.

`GET /api/reality/strategies?large_model_mb=nan` (and `-1024`) went through
`max(0.0, float(value))`, became 0.0 — "declares nothing" — and the
large-model path was ranked with no memory measured. Pure-generator tests
covered NaN/negative fail-closed; the route was the uncovered input path.

Invariants at the route: unmeasured != zero, stale != sufficient,
invalid requirement != no requirement.
"""
from __future__ import annotations

import pytest

from bcc.features import reality
from bcc.reality import strategy


async def ranked(env, **params):
    r = await env.client.get("/api/reality/strategies", params=params)
    return r, [x["strategy_id"] for x in (r.json().get("ranked", []) if r.status_code == 200 else [])]


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "-1024", "-0.5", "null", "abc", "", "1e999"])
async def test_an_invalid_requirement_is_a_client_error_not_a_zero(env, value):
    r, ids = await ranked(env, large_model_mb=value)
    assert r.status_code == 422, (value, r.text)
    assert "large-model-tools" not in ids


@pytest.mark.parametrize("value", ["nan", "-1"])
async def test_the_small_model_requirement_is_validated_the_same_way(env, value):
    r, _ = await ranked(env, small_model_mb=value)
    assert r.status_code == 422


async def test_zero_still_means_the_path_declares_nothing(env):
    r, ids = await ranked(env, large_model_mb="0")
    assert r.status_code == 200 and "large-model-tools" in ids


async def test_an_oversized_model_is_refused_when_memory_is_unmeasured(env):
    """No observation has run in this test process: the reading is unmeasured,
    and an unmeasured budget cannot be shown to hold 70 GB."""
    r, ids = await ranked(env, large_model_mb="70000")
    assert r.status_code == 200
    body = r.json()
    assert body["memory"]["measured"] is False and body["memory"]["available_mb"] is None
    assert "large-model-tools" not in ids
    unavailable = {x["strategy_id"]: x for x in body.get("unavailable", [])} if "unavailable" in body else {}
    assert "large-model-tools" in unavailable or "large-model-tools" not in ids


async def test_a_stale_measurement_does_not_admit_the_path(env, monkeypatch):
    monkeypatch.setattr(reality, "memory_reading",
                        lambda svc, now=None: strategy.MemoryReading(None, "process.host stale"))
    r, ids = await ranked(env, large_model_mb="1024")
    assert r.status_code == 200 and "large-model-tools" not in ids
    assert r.json()["memory"]["measured"] is False


async def test_a_fresh_sufficient_measurement_admits_what_fits(env, monkeypatch):
    monkeypatch.setattr(reality, "memory_reading",
                        lambda svc, now=None: strategy.MemoryReading(32000.0, "observer:process"))
    r, ids = await ranked(env, large_model_mb="1024")
    assert r.status_code == 200 and "large-model-tools" in ids
    r, ids = await ranked(env, large_model_mb="70000")
    assert r.status_code == 200 and "large-model-tools" not in ids
