"""ASTER6 audit reproducers; opt-in, MOCK_ENGINE only, never live evidence.

Run explicitly: python -m pytest command-center/tests/aster6_repro_media.py -q -s
Kept outside default test_*.py discovery until the integrator fixes the findings.
"""
import pytest

from bcc.features.images import process_one
from bcc.studio import catalog
from .test_studio_sdcpp_provider import engine, _job  # noqa: F401
from .test_studio_sdcpp_hostile import harness, plane  # noqa: F401

WAN = "sdcpp:wan2.2-ti2v-5b"


async def test_short_decodable_output_must_not_complete_ten_second_request(env, engine):
    # Existing fixture emits 16 frames per segment instead of requested 81.
    # No verification function or fake/live flag is changed by this reproducer.
    jid = await _job(env, WAN, length="10s", width=640, height=352, steps=16, seed=7)
    await process_one(env.svc)
    job = (await env.client.get(f"/api/studio/jobs/{jid}")).json()
    runs = (await env.client.get("/api/studio/runs")).json()["items"]
    if runs:
        generation = runs[0]["provenance"]["settings_resolved"]["engine_trace"]["generation"]
        print({"status": job["status"], "verdict": job["studio"]["verdict"],
               "declared_s": generation["duration_s_declared"],
               "observed_s": generation["duration_s_observed"], "engine": "MOCK_ENGINE"})
    assert job["status"] == "failed", "Wrong frame count/duration accepted as completed"
    assert not runs, "Invalid output must not be published as a verified run"


@pytest.mark.parametrize("override", [60.0, 3660.0])
async def test_explicit_timeout_override_wins_even_when_equal_to_catalog(harness, override):
    provider = harness.provider(WAN)
    provider.hard_timeout_s = override
    receipt = await provider.submit(plane(WAN, width=1280, height=704, frames=81, steps=50))
    job = provider.job_record(receipt.request_id)
    try:
        print({"explicit_timeout_s": override, "effective_timeout_s": job["hard_timeout_s"]})
        assert job["hard_timeout_s"] == override
    finally:
        # Cancel before task gets a turn: no process/GPU launch is needed.
        await provider.cancel(receipt.request_id)


@pytest.mark.parametrize("field,value", [
    ("width", 10**12), ("height", 10**12), ("frames", 10**12),
    ("steps", 10**12), ("length", "100000s"), ("segments", 100000),
])
def test_catalog_rejects_insane_work_before_scaling(field, value):
    model = next(m for m in catalog.load()["models"] if m["id"] == WAN)
    with pytest.raises(ValueError):
        catalog.validate_settings(model, {field: value})
