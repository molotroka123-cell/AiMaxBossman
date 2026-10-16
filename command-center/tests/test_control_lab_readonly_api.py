"""The Control Lab's live view may read existing owner APIs only."""
from __future__ import annotations

import httpx


async def test_control_lab_read_sources_work_with_owner_session(env):
    paths = (
        "/api/capability-tree",
        "/api/system",
        "/api/tasks?limit=100",
        "/api/agents",
        "/api/spend",
        "/api/resources",
    )
    for path in paths:
        response = await env.client.get(path)
        assert response.status_code == 200, (path, response.text)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anonymous:
        for path in paths:
            response = await anonymous.get(path)
            assert response.status_code == 401, (path, response.text)

    capability = (await env.client.get("/api/capability-tree")).json()
    assert isinstance(capability.get("tree", {}).get("nodes"), list)
    spend = (await env.client.get("/api/spend")).json()
    if spend.get("enabled") is not True:
        assert spend.get("spent") is None
