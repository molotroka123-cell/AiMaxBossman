"""Security audit 24.10: a cloud model must never receive PC tools or PC tool output through a FALLBACK.

Live 24.10: agent 1 runs a local model with computer.observe/act + terminal.run/filesystem.write granted (AUTO) and has a
cloud fallback (nemotron :free on OpenRouter). When the local model fails, engine._call_model hands the SAME tool schemas
(and the run's tool results: screenshots, files) to the cloud fallback — the standing rule «cloud agents are chat-only;
personal media never leaves the PC» is broken by an outage. The cloud fallback must be refused for a run that has PC
tools unless the owner set `cloud_pc_access: true`; a tool-less run may still fall back (negative control).
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import agents as agents_t
from bcc.providers import ProviderError

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task


class _Down(FakeAdapter):
    async def chat(self, model, messages, **kw):
        self.calls += 1
        raise ProviderError("local model down", kind="network")


async def _stack(env, *, tools, perms=None):
    stack = await make_stack(env.client, max_steps=3, prompt="сделай снимок экрана")
    cloud_p = (await env.client.post("/api/providers", json={
        "name": "cloud", "kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-test-abcd"})).json()
    cloud_m = (await env.client.post("/api/models", json={
        "provider_id": cloud_p["id"], "name": "vendor/cloud-x", "alias": "cloud-x", "kind": "cloud",
        "price_in": 0.0, "price_out": 0.0})).json()
    # Mirror the live row: set fallback/tools/permissions directly, as an old config would have them.
    async with env.svc.db.session() as s:
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            fallback_model_id=cloud_m["id"], tools=tools, permissions=perms or {}))
        await s.commit()
    local, cloud = _Down(), ToolAdapter([("text", "готово из облака")])
    env.svc.registry.adapter_factory = lambda m, p: cloud if "openrouter" in (p.get("base_url") or "") else local
    return stack, cloud


async def test_cloud_fallback_gets_no_pc_tools(env):
    _install("test.observe", permission="computer.observe")
    stack, cloud = await _stack(env, tools=["test.observe"], perms={"computer.observe": True})
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    leaked = [t for t in cloud.seen_tools if t]
    assert not leaked, f"cloud fallback received PC tools: {leaked}"


async def test_negative_control_tool_less_run_may_still_fall_back_to_cloud(env):
    stack, cloud = await _stack(env, tools=[])
    status = await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert cloud.calls >= 1 and status == "completed", (cloud.calls, status)


async def test_owner_granted_cloud_pc_access_keeps_the_fallback(env):
    _install("test.observe", permission="computer.observe")
    stack, cloud = await _stack(env, tools=["test.observe"],
                                perms={"computer.observe": True, "cloud_pc_access": True})
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert cloud.calls >= 1 and any(cloud.seen_tools), "owner grant: fallback keeps working with tools"
