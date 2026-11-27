"""Local operator: installed Ollama models -> local chat agents with computer use and the Harness (27.11).

Fake Ollama = httpx.MockTransport answering /api/version, /api/tags, /api/show like Ollama 0.x does. The engine part
uses the real tool registry (computer.observe / computer.act from tools_computer) and the real tool loop with a
scripted model adapter, so the policy proven here is the production policy: observation granted, every desktop action
an approval, chat agents without tools, cloud tags never provisioned as local.
"""
from __future__ import annotations

import json

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import agents as agents_t
from bcc.local_operator import inventory as inv
from bcc.local_operator import provision as pv
from bcc.tools import REGISTRY, allowed_tools_for, decide_effect

from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task

TAGS = [
    {"name": "qwen3-coder:30b", "size": 18_000_000_000,
     "details": {"family": "qwen3moe", "parameter_size": "30.5B", "quantization_level": "Q4_K_M"}},
    {"name": "qwen2.5vl:7b", "size": 6_000_000_000,
     "details": {"family": "qwen25vl", "parameter_size": "8.3B", "quantization_level": "Q4_K_M"}},
    {"name": "gemma3:4b", "size": 3_300_000_000,
     "details": {"family": "gemma3", "parameter_size": "4.3B", "quantization_level": "Q4_K_M"}},
    {"name": "nomic-embed-text:latest", "size": 270_000_000,
     "details": {"family": "nomic-bert", "parameter_size": "137M", "quantization_level": "F16"}},
    {"name": "gpt-oss:120b-cloud", "size": 384,
     "details": {"family": "gptoss", "parameter_size": "116.8B", "quantization_level": "MXFP4"}},
    {"name": "old-gguf:latest", "size": 1_000_000_000, "details": {"parameter_size": "3B"}},
]
SHOW = {
    "qwen3-coder:30b": {"capabilities": ["completion", "tools"], "model_info": {"qwen3moe.context_length": 262144}},
    "qwen2.5vl:7b": {"capabilities": ["completion", "vision", "tools"], "model_info": {"qwen25vl.context_length": 128000}},
    "gemma3:4b": {"capabilities": ["completion", "vision"], "model_info": {"gemma3.context_length": 131072}},
    "nomic-embed-text:latest": {"capabilities": ["embedding"]},
    "gpt-oss:120b-cloud": {"capabilities": ["completion", "tools", "thinking"]},
    "old-gguf:latest": {"details": {"family": "llama"}},          # old Ollama: no capabilities list
}


def fake_ollama(*, tags=TAGS, show=SHOW, down=False, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path))
        if down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": tags})
        if request.url.path == "/api/show" and request.method == "POST":
            name = json.loads(request.content)["model"]
            return httpx.Response(200, json=show[name]) if name in show else httpx.Response(404, json={})
        return httpx.Response(404, json={"error": "not found"})
    return httpx.MockTransport(handler)


async def _inventory(**kw):
    return await inv.probe(("http://127.0.0.1:11434",), transport=fake_ollama(**kw))


# ---------------------------------------------------------------- inventory

async def test_inventory_reports_what_ollama_says_each_model_can_do():
    got = await _inventory()
    assert got.ok and got.version == "0.12.3" and got.v1_url == "http://127.0.0.1:11434/v1"
    by = {m.name: m for m in got.models}
    assert by["qwen3-coder:30b"].tools and not by["qwen3-coder:30b"].vision
    assert by["qwen3-coder:30b"].context_length == 262144 and by["qwen3-coder:30b"].params == 30.5e9
    assert by["qwen2.5vl:7b"].tools and by["qwen2.5vl:7b"].vision
    assert by["gemma3:4b"].vision and not by["gemma3:4b"].tools
    assert not by["nomic-embed-text:latest"].chat
    assert by["gpt-oss:120b-cloud"].cloud


async def test_unknown_capabilities_never_count_as_tools_but_still_chat():
    """Negative control for the capability rule: no `capabilities` list (old Ollama) is not a tool grant."""
    m = {x.name: x for x in (await _inventory()).models}["old-gguf:latest"]
    assert m.capabilities_source == "unknown" and not m.tools and not m.vision and m.chat


async def test_ollama_down_is_an_honest_failure_not_an_empty_list():
    got = await _inventory(down=True)
    assert not got.ok and got.models == [] and "Ollama не отвечает" in got.error


async def test_second_port_is_used_when_the_first_is_not_ollama():
    def handler(request):
        if request.url.port == 11434:
            return httpx.Response(200, text="<html>forwarder</html>")
        return fake_ollama().handler(request)
    got = await inv.probe(("http://127.0.0.1:11434", "http://127.0.0.1:11435"),
                          transport=httpx.MockTransport(handler))
    assert got.ok and got.base_url == "http://127.0.0.1:11435"


async def test_a_failing_show_keeps_the_model_with_unknown_capabilities():
    show = dict(SHOW)
    del show["gemma3:4b"]
    m = {x.name: x for x in (await _inventory(show=show)).models}["gemma3:4b"]
    assert m.capabilities_source == "unknown" and "404" in m.show_error and not m.tools


def test_parameter_count_unknown_is_none_not_zero():
    assert inv.parameter_count("30.5B") == 30.5e9 and inv.parameter_count("137M") == 137e6
    assert inv.parameter_count("") is None and inv.parameter_count("unknown") is None


def test_env_override_of_ollama_urls():
    assert inv.ollama_urls({"BOSSMAN_OLLAMA_URLS": "http://10.0.0.5:11434/ , "}) == ("http://10.0.0.5:11434",)
    assert inv.ollama_urls({}) == inv.DEFAULT_URLS


# ---------------------------------------------------------------- pure plan

async def test_plan_skips_cloud_and_embedding_and_keeps_existing_rows():
    models = (await _inventory()).models
    registered = [{"id": 7, "provider_id": 3, "name": "gemma3:4b", "alias": "ollama/gemma3:4b"},
                  {"id": 9, "provider_id": 99, "name": "x", "alias": "ollama/qwen3-coder:30b"}]
    plan = pv.plan_models(models, registered, 3)
    rep = {r["name"]: r for r in plan.model_report}
    assert rep["gpt-oss:120b-cloud"]["status"] == "skipped" and "облачный" in rep["gpt-oss:120b-cloud"]["reason"]
    assert rep["nomic-embed-text:latest"]["status"] == "skipped"
    assert rep["gemma3:4b"] == {"name": "gemma3:4b", "status": "exists", "model_id": 7}
    created = {r["name"]: r for r in plan.models_to_create}
    assert set(created) == {"qwen3-coder:30b", "qwen2.5vl:7b", "old-gguf:latest"}
    assert created["qwen3-coder:30b"]["alias"] == "ollama/qwen3-coder:30b-2"      # alias taken elsewhere
    assert created["qwen2.5vl:7b"]["caps"] == {"tools": True, "vision": True, "reasoning": False}
    assert all(r["price_in"] == 0.0 and r["kind"] == "local" for r in created.values())


async def test_plan_agents_pc_variant_only_for_tool_models_and_harness_on_the_largest():
    models = (await _inventory()).models
    ids = {"qwen3-coder:30b": 1, "qwen2.5vl:7b": 2, "gemma3:4b": 3, "old-gguf:latest": 4}
    create, report = pv.plan_agents(models, ids, [])
    names = {a["name"]: a for a in create}
    assert "Локально · gemma3:4b" in names and "Локально · gemma3:4b · ПК" not in names
    assert "Локально · old-gguf:latest · ПК" not in names
    pc = names["Локально · qwen2.5vl:7b · ПК"]
    assert pc["tools"] == pv.OPERATOR_TOOLS and pc["permissions"] == {"computer.observe": True}
    assert pc["fallback_model_id"] is None and "computer.control" not in pc["permissions"]
    assert names["Локально · qwen3-coder:30b"]["tools"] == []
    assert names[pv.HARNESS_NAME]["model_id"] == 1                          # 30.5B beats 8.3B
    assert not any("cloud" in a["name"] or "embed" in a["name"] for a in create)
    skipped = {r["agent"] for r in report if r["status"] == "skipped"}
    assert "Локально · gemma3:4b · ПК" in skipped


async def test_existing_harness_and_agents_are_left_alone_with_drift_reported():
    models = (await _inventory()).models
    ids = {"qwen3-coder:30b": 1, "qwen2.5vl:7b": 2}
    existing = [{"id": 80, "name": pv.HARNESS_NAME, "model_id": 102},
                {"id": 5, "name": "Локально · qwen2.5vl:7b", "model_id": 77}]
    create, report = pv.plan_agents(models, ids, existing)
    assert pv.HARNESS_NAME not in {a["name"] for a in create}
    assert "Локально · qwen2.5vl:7b" not in {a["name"] for a in create}
    rep = {r["agent"]: r for r in report}
    assert rep["Локально · qwen2.5vl:7b"]["status"] == "drift" and rep[pv.HARNESS_NAME]["status"] == "exists"


def test_no_tool_model_means_no_harness_with_a_reason():
    m = inv.LocalModel(name="gemma3:4b", capabilities=("completion",), capabilities_source="ollama")
    create, report = pv.plan_agents([m], {"gemma3:4b": 1}, [])
    assert pv.HARNESS_NAME not in {a["name"] for a in create}
    assert any(r["agent"] == pv.HARNESS_NAME and r["status"] == "skipped" for r in report)


def test_same_endpoint_normalises_localhost_and_path():
    assert pv.same_endpoint("http://localhost:11434/v1/", "http://127.0.0.1:11434/v1")
    assert not pv.same_endpoint("http://127.0.0.1:11435/v1", "http://127.0.0.1:11434/v1")
    assert not pv.same_endpoint("", "http://127.0.0.1:11434/v1")


# ---------------------------------------------------------------- routes + engine

async def _sync(env, **kw):
    env.svc._local_operator_transport = fake_ollama(**kw)
    r = await env.client.post("/api/local-operator/sync", json={})
    return r


async def test_sync_registers_models_and_agents_once(env):
    r = await _sync(env)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["provider_created"] is True
    created = {a["agent"]: a for a in out["agents"] if a["status"] == "create"}
    assert {"Локально · qwen3-coder:30b · ПК", "Локально · qwen2.5vl:7b · ПК", pv.HARNESS_NAME} <= set(created)
    models = (await env.client.get("/api/models")).json()
    names = {m["name"] for m in models}
    assert "gpt-oss:120b-cloud" not in names and "nomic-embed-text:latest" not in names
    prov = [p for p in (await env.client.get("/api/providers")).json() if p["id"] == out["provider_id"]][0]
    assert prov["base_url"] == "http://127.0.0.1:11434/v1"
    # idempotent: second sync creates nothing
    again = (await _sync(env)).json()
    assert again["provider_created"] is False
    assert not [a for a in again["agents"] if a["status"] == "create"]
    assert not [m for m in again["models"] if m["status"] == "create"]


async def test_sync_reuses_the_owners_existing_ollama_provider(env):
    p = (await env.client.post("/api/providers", json={
        "name": "мой ollama", "kind": "openai_compat", "base_url": "http://localhost:11434/v1"})).json()
    out = (await _sync(env)).json()
    assert out["provider_id"] == p["id"] and out["provider_created"] is False


async def test_sync_with_ollama_down_is_503_and_changes_nothing(env):
    before = len((await env.client.get("/api/agents")).json())
    r = await _sync(env, down=True)
    assert r.status_code == 503 and r.json()["code"] == "OLLAMA_UNREACHABLE"
    assert len((await env.client.get("/api/agents")).json()) == before


async def test_status_lists_models_with_registration(env):
    env.svc._local_operator_transport = fake_ollama()
    st = (await env.client.get("/api/local-operator/status")).json()
    assert st["ollama"]["ok"] and st["provider_id"] is None
    await _sync(env)
    st = (await env.client.get("/api/local-operator/status")).json()
    reg = {m["name"]: m["model_id"] for m in st["ollama"]["models"]}
    assert reg["qwen3-coder:30b"] and reg["gpt-oss:120b-cloud"] is None
    assert any(a["name"] == pv.HARNESS_NAME for a in st["agents"])


async def _agent(env, name):
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.name == name))).first()
    return dict(row._mapping)


async def test_policy_observe_is_granted_every_desktop_action_is_an_approval(env):
    await _sync(env)
    pc = await _agent(env, "Локально · qwen2.5vl:7b · ПК")
    chat = await _agent(env, "Локально · qwen2.5vl:7b")
    assert set(allowed_tools_for({"meta": {}}, pc)) == set(pv.OPERATOR_TOOLS)
    assert allowed_tools_for({"meta": {}}, chat) == []
    observe, act = REGISTRY.get("computer.observe"), REGISTRY.get("computer.act")
    assert observe is not None and act is not None, "tools_computer must be registered"
    assert decide_effect(observe, {}, pc)[0] == "auto"
    for args in ({"action": "click", "target": "OK"}, {"action": "type", "text": "hi"},
                 {"action": "launch", "target": "notepad"}):
        assert decide_effect(act, args, pc)[0] == "ask", args
    # negative control: an agent WITHOUT the observe grant is asked even to look
    assert decide_effect(observe, {}, {**pc, "permissions": {}})[0] == "ask"


async def test_harness_tools_resolve_to_registered_tools_only(env):
    await _sync(env)
    h = await _agent(env, pv.HARNESS_NAME)
    resolved = {s.name for s in REGISTRY.resolve(allowed_tools_for({"meta": {}}, h))}
    assert {"computer.observe", "computer.act"} <= resolved
    assert all(n.split(".")[0] in {"code", "guard", "computer", "terminal"} for n in resolved)
    assert h["fallback_model_id"] is None


async def _task(env, agent_id, prompt="сделай"):
    return (await env.client.post("/api/tasks", json={"title": "локально", "prompt": prompt, "agent_id": agent_id,
                                                      "run_now": True, "max_retries": 0})).json()["task"]


async def test_engine_run_desktop_click_waits_for_the_owner(env):
    await _sync(env)
    pc = await _agent(env, "Локально · qwen2.5vl:7b · ПК")
    adapter = ToolAdapter([("tool", "computer_act", {"action": "click", "target": "OK", "generation": 1}),
                           ("text", "готово")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    task = await _task(env, pc["id"])
    status = await _run_task(env, task["id"])
    assert status == "waiting_approval"
    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and "computer.act" in appr[0]["preview"]
    assert adapter.seen_tools[0] and {t["function"]["name"] for t in adapter.seen_tools[0]} == {
        "computer_observe", "computer_act", "computer_processes"}


async def test_engine_run_observe_needs_no_approval(env):
    await _sync(env)
    pc = await _agent(env, "Локально · qwen3-coder:30b · ПК")
    adapter = ToolAdapter([("tool", "computer_observe", {}), ("text", "экран посмотрел")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    task = await _task(env, pc["id"])
    status = await _run_task(env, task["id"], timeout=10, until=FINISHED)
    assert status == "completed"
    assert (await env.client.get("/api/approvals")).json() == []
    assert adapter.calls >= 2          # the observation result went back to the model


async def test_engine_chat_agent_gets_no_tools(env):
    await _sync(env)
    chat = await _agent(env, "Локально · gemma3:4b")
    adapter = ToolAdapter([("text", "привет")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    task = await _task(env, chat["id"])
    assert await _run_task(env, task["id"], timeout=10, until=FINISHED) == "completed"
    assert not any(adapter.seen_tools)


@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "kimi-k2:1t-cloud", "x:cloud"])
def test_cloud_tags_are_never_planned(name):
    m = inv.parse_model({"name": name}, {"capabilities": ["completion", "tools"]})
    plan = pv.plan_models([m], [], 1)
    assert plan.models_to_create == [] and plan.model_report[0]["status"] == "skipped"
