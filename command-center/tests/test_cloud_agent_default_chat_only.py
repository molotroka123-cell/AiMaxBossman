"""Security audit 24.10: a cloud-model agent is chat-only by DEFAULT, not only when someone remembers the flag.

`chat_only()` looks only at `permissions.cloud_pc_access`. Before this fix `POST /api/agents` with a cloud model
and no `permissions` (the API default is `{}`) produced an agent whose tools (code.search, fs.read, terminal.*)
were all handed to the cloud model — the standing rule «cloud agents are chat-only» was opt-in. Same for
`PATCH` of a local agent with 13 tools onto a cloud model. The owner's explicit grant (agent 47:
`cloud_pc_access: true`) must survive; a local agent must stay untouched (negative controls).
"""
from __future__ import annotations

from bcc.tools import allowed_tools_for, chat_only

TOOLS = ["code.search", "terminal.run"]


async def _model(env, base_url: str, name: str) -> int:
    p = (await env.client.post("/api/providers", json={
        "name": f"p-{name}", "kind": "openai_compat", "base_url": base_url, "api_key": "sk-test-abcd"})).json()
    m = (await env.client.post("/api/models", json={"provider_id": p["id"], "name": name, "alias": name})).json()
    return int(m["id"])


async def _agent(env, model_id: int, **extra) -> dict:
    r = await env.client.post("/api/agents", json={"name": "a", "model_id": model_id, "tools": TOOLS, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def test_cloud_model_agent_without_flag_is_chat_only(env):
    agent = await _agent(env, await _model(env, "https://openrouter.ai/api/v1", "z-ai/glm-x"))
    assert agent["permissions"].get("cloud_pc_access") is False, agent["permissions"]
    assert chat_only(agent) and allowed_tools_for({}, agent) == []


async def test_ollama_cloud_offload_on_loopback_counts_as_cloud(env):
    agent = await _agent(env, await _model(env, "http://127.0.0.1:11434/v1", "minimax-m9:cloud"))
    assert chat_only(agent)


async def test_local_agent_with_cloud_fallback_keeps_local_tools(env):
    # The cloud fallback itself is refused at run time for a run with PC tools (test_cloud_fallback_no_pc_tools).
    local = await _model(env, "http://127.0.0.1:8080/v1", "local-7b")
    cloud = await _model(env, "https://openrouter.ai/api/v1", "cloud-fb")
    agent = await _agent(env, local, fallback_model_id=cloud)
    assert not chat_only(agent) and allowed_tools_for({}, agent) == TOOLS


async def test_owner_explicit_grant_is_kept(env):
    agent = await _agent(env, await _model(env, "https://7api.example/v1", "kimi-x"),
                         permissions={"cloud_pc_access": True})
    assert agent["permissions"]["cloud_pc_access"] is True
    assert allowed_tools_for({}, agent) == TOOLS


async def test_negative_control_local_agent_keeps_its_tools_and_gets_no_flag(env):
    agent = await _agent(env, await _model(env, "http://127.0.0.1:8080/v1", "local-7b"))
    assert "cloud_pc_access" not in (agent["permissions"] or {})
    assert allowed_tools_for({}, agent) == TOOLS


async def test_patch_local_agent_onto_cloud_model_becomes_chat_only(env):
    agent = await _agent(env, await _model(env, "http://127.0.0.1:8080/v1", "local-7b"))
    cloud = await _model(env, "https://openrouter.ai/api/v1", "cloud-y")
    r = await env.client.patch(f"/api/agents/{agent['id']}", json={"model_id": cloud})
    assert r.status_code == 200, r.text
    assert chat_only(r.json()) and allowed_tools_for({}, r.json()) == []
    # A later permissions PATCH that forgets the flag must not reopen PC access either.
    r = await env.client.patch(f"/api/agents/{agent['id']}", json={"permissions": {"filesystem.read": True}})
    assert chat_only(r.json()), r.json()["permissions"]


async def test_revoking_an_owner_grant_by_patch_is_not_undone(env):
    agent = await _agent(env, await _model(env, "https://7api.example/v1", "kimi-z"),
                         permissions={"cloud_pc_access": True})
    r = await env.client.patch(f"/api/agents/{agent['id']}", json={"permissions": {"filesystem.read": True}})
    assert chat_only(r.json()), "a True grant must not be carried over when the owner rewrites permissions"
