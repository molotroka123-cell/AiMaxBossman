"""Feature 10 — Skill Library + MCP Hub."""
from bcc.v2.mcp_hub import MCPServerSpec, namespaced_tool

from .conftest import FakeAdapter
from .helpers import make_stack

SKILL_V1 = """---
name: website-audit
description: Аудит сайта
input_schema:
  required: [website_url]
  properties:
    website_url: {type: string}
output_schema:
  properties:
    score: {type: number}
---
# Website Audit
1. открыть homepage
2. проверить CTA
"""


def test_mcp_namespacing():
    assert namespaced_tool("My Server", "read tool") == "mcp:My_Server:read_tool"
    assert MCPServerSpec("s", "s", "stdio").validate() == ["stdio MCP requires command"]


async def test_discover_bundled_skills(env):
    # укажем библиотеку на реальный repo (в env ui_dir=tmp, поэтому дефолт не туда)
    import pathlib
    from bcc.v2.skill_library import SkillLibrary
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    env.svc.skills = SkillLibrary([repo / ".agents" / "skills"], repo / ".agents" / "skills")
    skills = (await env.client.get("/api/skills")).json()
    ids = {s["id"] for s in skills}
    # 8 портируемых скиллов из пака должны обнаружиться (без рекурсии по машине)
    assert {"repo-audit", "safe-terminal", "model-eval", "night-mission"} <= ids


async def test_skill_versions_and_run_influences_prompt(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter('{"score": 9}')
    # создать v1
    v1 = (await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL_V1})).json()
    assert v1["id"] == "website-audit"
    # запустить на агенте → prompt содержит process + input
    stack = await make_stack(env.client)
    run = (await env.client.post("/api/skills/website-audit/run",
                                 json={"input": {"website_url": "http://x"},
                                       "agent_id": stack["agent"]["id"]})).json()
    from bcc.db import tasks as tasks_t
    import sqlalchemy as sa
    async with env.svc.db.session() as s:
        prompt = (await s.execute(sa.select(tasks_t.c.prompt, tasks_t.c.meta)
                                  .where(tasks_t.c.id == run["task_id"]))).first()._mapping
    assert "Website Audit" in prompt["prompt"] and "http://x" in prompt["prompt"]
    assert prompt["meta"]["skill"] == "website-audit"


async def test_skill_run_rejects_bad_input(env):
    await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL_V1})
    r = await env.client.post("/api/skills/website-audit/run", json={"input": {}})
    assert r.status_code == 422    # нет обязательного website_url


async def test_clone_export_import_roundtrip(env):
    await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL_V1})
    clone = (await env.client.post("/api/skills/website-audit/clone",
                                   json={"new_id": "website-audit-2"})).json()
    assert clone["id"] == "website-audit-2"
    exported = (await env.client.get("/api/skills/website-audit/export")).json()
    imp = (await env.client.post("/api/skills/import",
                                 json={"id": "website-audit-3", "content": exported["content"]})).json()
    assert imp["id"] == "website-audit-3"


async def test_assign_skill_to_agent(env):
    await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL_V1})
    stack = await make_stack(env.client)
    r = (await env.client.post("/api/skills/website-audit/assign",
                               json={"agent_id": stack["agent"]["id"]})).json()
    assert stack["agent"]["id"] in r["agents"]
    skills = (await env.client.get("/api/skills")).json()
    wa = next(s for s in skills if s["id"] == "website-audit")
    assert stack["agent"]["id"] in wa["agents"]


async def test_mcp_registry_and_policy(env):
    srv = (await env.client.post("/api/mcp/servers",
                                 json={"name": "fs", "transport": "stdio",
                                       "command": ["mcp-fs"]})).json()
    assert srv["id"]
    bad = await env.client.post("/api/mcp/servers", json={"name": "x", "transport": "http"})
    assert bad.status_code == 422       # http без url
    # политика инструмента AUTO/ASK/DENY
    r = (await env.client.post("/api/mcp/policy",
                               json={"canonical": "mcp:fs:read", "policy": "deny"})).json()
    assert r["policy"] == "deny"
    r2 = await env.client.post("/api/mcp/policy",
                               json={"canonical": "mcp:fs:read", "policy": "bogus"})
    assert r2.status_code == 422
