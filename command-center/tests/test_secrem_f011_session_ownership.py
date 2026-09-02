"""SECREM F-011 — сессии браузера/OpenCode принадлежат задаче.

REPRO (Fable 5.1): browser.* с явным session_id чужой задачи управлял её
браузером; opencode find_session(session_id=…) отдавал сессию любой задачи.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import utcnow
from bcc.features import tools_browser, tools_opencode
from bcc.tools import ToolContext
from bcc.v2.tables import browser_sessions as bs_t

from .helpers import make_stack


async def _other_task(env, stack) -> dict:
    """Вторая задача того же агента — «чужая» для первой."""
    r = await env.client.post("/api/tasks", json={"title": "другая", "prompt": "x",
                                                  "agent_id": stack["agent"]["id"]})
    return r.json()["task"]


async def test_repro_browser_explicit_foreign_session_is_refused(env):
    mine = await make_stack(env.client)
    other = await _other_task(env, mine)
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(bs_t).values(
            task_id=other["id"], agent_id=mine["agent"]["id"], status="running",
            created_at=utcnow(), updated_at=utcnow()))
        foreign_sid = int(res.inserted_primary_key[0])
        await s.commit()
    ctx = ToolContext(svc=env.svc, task={"id": mine["task"]["id"]}, run_id=1, agent=mine["agent"])
    touched = []

    class Mgr:
        async def navigate(self, sid, url, **kw):
            touched.append(sid)
            return {"url": url}
    env.svc.browser = Mgr()
    res = await tools_browser._open({"url": "https://example.com/", "session_id": foreign_sid}, ctx)
    assert res.error is True and touched == []
    assert "не принадлежит" in res.content or "сессия не открылась" in res.content


async def test_variant_opencode_session_lookup_is_task_scoped(env):
    mine = await make_stack(env.client)
    other = await _other_task(env, mine)
    await tools_opencode.record_session(env.svc, session_id="oc-foreign", task_id=other["id"],
                                        run_id=None, project_path="/tmp/x", worktree_path="/tmp/x/wt")
    assert await tools_opencode.find_session(env.svc, "oc-foreign", task_id=mine["task"]["id"]) is None
    row = await tools_opencode.find_session(env.svc, "oc-foreign", task_id=other["id"])
    assert row and row["session_id"] == "oc-foreign"
