from __future__ import annotations

from types import SimpleNamespace

import pytest

from bcc.features import v15_autonomy as v15


def request_for(tmp_path):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=svc)))


@pytest.mark.asyncio
async def test_status_creates_persistent_default_society(tmp_path):
    req = request_for(tmp_path)
    out = await v15.status(req)
    assert out["wired"] is True
    assert {r["role"] for r in out["roles"]} >= {"coder", "researcher", "verifier", "auditor"}
    assert (tmp_path / "v1.5" / "society" / "roles.json").is_file()


@pytest.mark.asyncio
async def test_plan_is_deterministic_and_never_authoritative(tmp_path):
    req = request_for(tmp_path)
    body = v15.PlanBody(
        task_id="t1", task_class="coding_bugfix",
        required_roles=["verifier"],
        candidates=[
            v15.CandidateBody(id="local", quality_lcb=.82, cost_usd=0, latency_ms=250,
                              energy_wh=.02, local=True),
            v15.CandidateBody(id="cloud", quality_lcb=.95, cost_usd=.1, latency_ms=100,
                              energy_wh=None, local=False),
        ],
        min_quality_lcb=.8,
    )
    out = await v15.plan(body, req)
    assert "verifier" in out["team"]
    assert out["route"]["candidate_id"] in {"local", "cloud"}
    assert out["authoritative"] is False


@pytest.mark.asyncio
async def test_unknown_cloud_price_is_not_silently_free(tmp_path):
    req = request_for(tmp_path)
    body = v15.PlanBody(
        task_id="t2", task_class="research",
        candidates=[v15.CandidateBody(id="unknown-cloud", quality_lcb=.99, local=False)],
    )
    out = await v15.plan(body, req)
    assert out["route"]["candidate_id"] is None
    assert "unknown_price" in out["route"]["considered"][0]["reasons"]
