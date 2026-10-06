"""authored_by_lane agcloud: behavior tests for bcc.features.organization (default-OFF contract)."""
from __future__ import annotations

import pytest

from bcc.features import organization


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    monkeypatch.delenv("BOSSMAN_V3_ENABLED", raising=False)
    monkeypatch.delenv("BOSSMAN_V3_ORGANIZATION", raising=False)
    monkeypatch.delenv("BOSSMAN_V3_FLEET", raising=False)


def test_enabled_requires_both_flags(monkeypatch):
    assert organization.enabled() is False
    monkeypatch.setenv("BOSSMAN_V3_ENABLED", "1")
    assert organization.enabled() is False                     # one flag is not enough
    monkeypatch.setenv("BOSSMAN_V3_ORGANIZATION", "yes")
    assert organization.enabled() is True
    assert organization.fleet_enabled() is False
    monkeypatch.setenv("BOSSMAN_V3_FLEET", "on")
    assert organization.fleet_enabled() is True
    monkeypatch.setenv("BOSSMAN_V3_ORGANIZATION", "0")
    assert organization.enabled() is False and organization.fleet_enabled() is False


async def test_all_routes_503_when_disabled_and_nothing_created(env):
    assert env.svc.organization is None
    for method, path in (("get", "/api/org/snapshot"), ("get", "/api/org/departments"), ("get", "/api/org/agents"),
                         ("get", "/api/org/missions"), ("get", "/api/org/learning"), ("get", "/api/org/fleet"),
                         ("post", "/api/org/resume")):
        r = await getattr(env.client, method)(path)
        assert r.status_code == 503, (path, r.status_code)
        assert "organization" in r.text
    r = await env.client.post("/api/org/missions", json={"department_id": "x", "goal": "y"})
    assert r.status_code == 503
