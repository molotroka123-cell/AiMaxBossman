"""POST /api/skills/{id}/assign must not report success for garbage, and must
not wipe stored assignments it could not read.

Before the fix the route answered 200 for a skill id that does not exist and
for an agent id that does not exist, and an unreadable assignments row was
read as {} and then OVERWRITTEN with a one-entry dict (every other
assignment gone). Each strictness rule is paired with the legitimate case
(negative-control).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import settings_kv
from bcc.features.skills import ASSIGN_KEY

SKILL = """---
name: website-audit
description: Аудит сайта
---
# Website Audit
1. открыть homepage
"""


@pytest.fixture(autouse=True)
def skills_live_in_a_temporary_library(env, tmp_path):
    """Same isolation as test_feat_skills: never write into the repo's .agents/skills."""
    from bcc.v2.skill_library import SkillLibrary, default_skill_roots
    workspace = tmp_path / "skills-workspace"
    (workspace / ".agents" / "skills").mkdir(parents=True)
    env.svc.skills = SkillLibrary(default_skill_roots(workspace, home=tmp_path / "home"),
                                  workspace / ".agents" / "skills")


async def _agent(client) -> int:
    provider = (await client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-test-abcd"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})).json()
    agent = (await client.post("/api/agents", json={
        "name": "аналитик", "system_prompt": "отвечай коротко", "model_id": model["id"]})).json()
    return int(agent["id"])


async def _listed_agents(client, skill_id: str) -> list:
    skills = (await client.get("/api/skills")).json()
    return next(s for s in skills if s["id"] == skill_id)["agents"]


async def test_assign_existing_skill_to_existing_agent_is_idempotent(env):
    """Legitimate case: passes, survives a repeat, and a numeric string is the same agent."""
    r = await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL})
    assert r.status_code == 200, r.text
    aid = await _agent(env.client)
    r = await env.client.post("/api/skills/website-audit/assign", json={"agent_id": aid})
    assert r.status_code == 200, r.text
    assert r.json()["agents"] == [aid]
    r = await env.client.post("/api/skills/website-audit/assign", json={"agent_id": str(aid)})
    assert r.status_code == 200, r.text
    assert r.json()["agents"] == [aid]            # no "3" next to 3
    assert await _listed_agents(env.client, "website-audit") == [aid]


async def test_assign_unknown_skill_is_404_and_stores_nothing(env):
    aid = await _agent(env.client)
    r = await env.client.post("/api/skills/no-such-skill/assign", json={"agent_id": aid})
    assert r.status_code == 404, r.text
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == ASSIGN_KEY))).first()
    assert row is None


@pytest.mark.parametrize("agent_id,code", [(999_999, 404), ("abc", 422), (None, 422)])
async def test_assign_to_missing_or_malformed_agent_is_refused(env, agent_id, code):
    await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL})
    r = await env.client.post("/api/skills/website-audit/assign", json={"agent_id": agent_id})
    assert r.status_code == code, r.text
    assert await _listed_agents(env.client, "website-audit") == []


async def test_unreadable_assignments_are_not_overwritten(env):
    await env.client.post("/api/skills", json={"id": "website-audit", "content": SKILL})
    aid = await _agent(env.client)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(key=ASSIGN_KEY, value_enc="garbage"))
        await s.commit()
    r = await env.client.post("/api/skills/website-audit/assign", json={"agent_id": aid})
    assert r.status_code == 503, r.text
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == ASSIGN_KEY))).first()
    assert row[0] == "garbage"                    # left for recovery, not replaced
    # the read-only listing stays usable (shows no assignments rather than 500)
    r = await env.client.get("/api/skills")
    assert r.status_code == 200
