"""Lab agents are real, idempotently seeded ``agents`` rows with distinct configs."""
from __future__ import annotations

from bcc.features import lab_agents as la


async def _agents(client):
    return (await client.get("/api/agents")).json()


async def test_ensure_twice_leaves_six_rows_not_twelve(env):
    first = await la.ensure_lab_agents(env.svc)
    assert sorted(first["created"]) == sorted(la.VARIANTS)
    second = await la.ensure_lab_agents(env.svc)
    assert second["created"] == [] and second["updated"] == []
    assert sorted(second["unchanged"]) == sorted(la.VARIANTS)
    rows = [r for r in await _agents(env.client) if r["role"] == la.LAB_ROLE]
    assert len(rows) == 6
    assert sorted(r["name"] for r in rows) == sorted(la.agent_name(v) for v in la.VARIANTS)


async def test_configs_are_distinct_and_only_memory_recalls(env):
    agents = (await la.ensure_lab_agents(env.svc))["agents"]
    by_variant = {a["variant"]: a for a in agents}
    assert set(by_variant) == set(la.VARIANTS)
    prompts = {a["system_prompt"] for a in agents}
    assert len(prompts) == 6
    configs = {(a["system_prompt"], tuple(a["tools"]), a["max_steps"],
                tuple(sorted(a["permissions"].items()))) for a in agents}
    assert len(configs) == 6
    assert [v for v, a in by_variant.items() if a["use_memory"]] == ["MEMORY"]
    assert by_variant["RAW"]["permissions"]["require_tests_before_finish"] is False
    assert by_variant["TOOL_FIRST"]["permissions"]["require_tests_before_finish"] is True
    assert "write_file" not in by_variant["USER_UX"]["tools"]
    for a in agents:
        assert set(a["tools"]) <= set(la.SIDECAR_TOOLS)
        assert a["max_steps"] > 4


async def test_profile_matches_the_coding_tasks_contract(env):
    agents = (await la.ensure_lab_agents(env.svc))["agents"]
    mem = next(a for a in agents if a["variant"] == "MEMORY")
    prof = la.agent_profile(mem)
    assert set(prof) == {"name", "system_prompt", "max_steps", "tools", "require_tests_before_finish"}
    assert prof["name"] == "LAB · MEMORY" and prof["require_tests_before_finish"] is True
    assert prof["tools"] == list(la.SIDECAR_TOOLS)


async def test_drift_is_repaired_and_owner_model_is_kept(env):
    provider = (await env.client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat", "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "sk-test-abcd"})).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})).json()
    agents = (await la.ensure_lab_agents(env.svc, model_id=model["id"]))["agents"]
    assert {a["model_id"] for a in agents} == {model["id"]}
    raw = next(a for a in agents if a["variant"] == "RAW")
    # owner (or anyone) edits the prompt through the product API: the lab config drifted
    r = await env.client.patch(f"/api/agents/{raw['id']}", json={"system_prompt": "что-то другое",
                                                                   "tools": ["read_file"]})
    assert r.status_code == 200
    again = await la.ensure_lab_agents(env.svc)
    assert again["updated"] == ["RAW"] and again["created"] == []
    raw2 = next(a for a in again["agents"] if a["variant"] == "RAW")
    assert raw2["id"] == raw["id"]
    assert raw2["system_prompt"] == la.SPECS["RAW"]["system_prompt"]
    assert raw2["model_id"] == model["id"]          # not reset by a call without model_id


async def test_http_routes(env):
    r = await env.client.post("/api/lab-agents/ensure", json={})
    assert r.status_code == 200, r.text
    assert len(r.json()["agents"]) == 6
    r = await env.client.post("/api/lab-agents/ensure", json={})
    assert r.json()["created"] == []
    listed = (await env.client.get("/api/lab-agents")).json()
    assert len(listed["items"]) == 6 and listed["variants"] == list(la.VARIANTS)
    missing = await env.client.post("/api/lab-agents/ensure", json={"model_id": 999})
    assert missing.status_code == 404


async def test_ordinary_agents_are_not_touched(env):
    mine = (await env.client.post("/api/agents", json={"name": "Решатель", "system_prompt": "x"})).json()
    await la.ensure_lab_agents(env.svc)
    await la.ensure_lab_agents(env.svc)
    rows = await _agents(env.client)
    assert [r for r in rows if r["id"] == mine["id"]][0]["system_prompt"] == "x"
    assert len(rows) == 7
