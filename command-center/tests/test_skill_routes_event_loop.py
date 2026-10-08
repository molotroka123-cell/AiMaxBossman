"""Skills routes keep the event loop free while the library/catalog touches the disk.

Before the fix these routes called the disk-bound skill library directly on the
event loop: GET /api/skills -> SkillLibrary.discover() (globs and stats six roots,
including ~/.agents, ~/.claude and ~/.config/opencode, and re-parses YAML after any
mtime change); GET /api/skills/{id}, clone, export and run -> by_id() plus
read_text(); GET /api/skill-catalog/{source}/{skill} and the catalog revoke route ->
SkillCatalog.get(), which re-reads, hashes and policy-scans every catalog SKILL.md.
While that ran, every other Command Center request (dashboard polls, SSE, the engine)
waited.

The probe is deterministic, not a stopwatch: the patched disk call BLOCKS its thread
until a coroutine on the event loop opens a gate. If the call runs on the loop, that
coroutine cannot run until the gate times out (red). If it runs in a worker thread,
the coroutine opens the gate at once (green). Results and error mapping (200 bodies,
404, 409) must stay what they were.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from bcc.v2.skill_catalog import SkillCatalog
from bcc.v2.skill_library import SkillLibrary, default_skill_roots

from .test_skill_catalog import GOOD, write_skill

GATE_TIMEOUT = 5.0

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
    workspace = tmp_path / "skills-workspace"
    (workspace / ".agents" / "skills").mkdir(parents=True)
    env.svc.skills = SkillLibrary(default_skill_roots(workspace, home=tmp_path / "home"),
                                  workspace / ".agents" / "skills")
    env.svc.skills.create("website-audit", SKILL)
    root = tmp_path / "cat"
    write_skill(root, "good", "widget-care", GOOD)
    env.svc.skill_catalog = SkillCatalog(root)


class LoopProbe:
    """Wraps a disk-bound callable so it waits for the event loop to answer."""

    def __init__(self):
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.opened_in_time: list[bool] = []

    def wrap(self, fn):
        def blocking(*args, **kwargs):
            self.entered.set()
            self.opened_in_time.append(self.gate.wait(GATE_TIMEOUT))
            return fn(*args, **kwargs)
        return blocking

    async def opener(self):
        # Runs on the event loop: it can open the gate only while the loop is free.
        deadline = asyncio.get_running_loop().time() + 2 * GATE_TIMEOUT
        while not self.entered.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        self.gate.set()


async def _probe(env, target, method, url, **kw):
    probe = LoopProbe()
    target.discover = probe.wrap(target.discover)   # by_id() and entries() call it too
    try:
        response, _ = await asyncio.gather(env.client.request(method, url, **kw),
                                           probe.opener())
    finally:
        del target.discover
    assert probe.entered.is_set(), f"{method} {url} never reached the disk call"
    assert all(probe.opened_in_time), (
        f"{method} {url} blocked the event loop: the loop could not run another "
        f"coroutine for {GATE_TIMEOUT:.0f} s while the skill library touched the disk")
    return response


@pytest.mark.parametrize("method,url,body", [
    ("GET", "/api/skills", None),
    ("GET", "/api/skills/website-audit", None),
    ("POST", "/api/skills/website-audit/clone", {"new_id": "website-audit-2"}),
    ("GET", "/api/skills/website-audit/export", None),
    ("POST", "/api/skills/website-audit/run", {"input": {}}),
])
async def test_library_routes_leave_the_loop_free(env, method, url, body):
    kw = {"json": body} if body is not None else {}
    r = await _probe(env, env.svc.skills, method, url, **kw)
    assert r.status_code == 200, r.text
    data = r.json()
    if url == "/api/skills":
        assert [s["id"] for s in data] == ["website-audit"] and data[0]["agents"] == []
    elif url.endswith("/clone"):
        assert data["id"] == "website-audit-2"
        assert "website-audit-2" in env.svc.skills.by_id()
    elif url.endswith("/export"):
        assert data["id"] == "website-audit" and data["content"] == SKILL
    elif url.endswith("/run"):
        assert data["skill"] == "website-audit" and isinstance(data["task_id"], int)
    else:
        assert data["id"] == "website-audit" and "Website Audit" in data["process"]


@pytest.mark.parametrize("method,url,body", [
    ("GET", "/api/skill-catalog", None),   # control: this route was already threaded
    ("GET", "/api/skill-catalog/good/widget-care", None),
    ("POST", "/api/skill-catalog/good/widget-care/revoke", {"reason": "проверка"}),
])
async def test_catalog_routes_leave_the_loop_free(env, method, url, body):
    kw = {"json": body} if body is not None else {}
    r = await _probe(env, env.svc.skill_catalog.library, method, url, **kw)
    assert r.status_code == 200, r.text
    data = r.json()
    if url == "/api/skill-catalog":
        assert [e["id"] for e in data] == ["good/widget-care"]
    elif url.endswith("/revoke"):
        assert data == {"id": "good/widget-care", "revoked": data["revoked"]}
        assert data["revoked"]["sha256"] == "*" and data["revoked"]["reason"] == "проверка"
    else:
        assert data["id"] == "good/widget-care" and "Widget care" in data["text"]


@pytest.mark.parametrize("method,url,body,status", [
    ("GET", "/api/skills/nope", None, 404),
    ("POST", "/api/skills/nope/clone", {}, 404),
    ("GET", "/api/skills/nope/export", None, 404),
    ("POST", "/api/skills/nope/run", {"input": {}}, 404),
    ("POST", "/api/skills/website-audit/clone", {"new_id": "website-audit"}, 409),
    ("POST", "/api/skills/website-audit/clone", {"new_id": "Плохое имя"}, 409),
    ("GET", "/api/skill-catalog/nope/nothing", None, 404),
    ("POST", "/api/skill-catalog/nope/nothing/revoke", {}, 404),
])
async def test_error_mapping_is_unchanged(env, method, url, body, status):
    kw = {"json": body} if body is not None else {}
    r = await env.client.request(method, url, **kw)
    assert r.status_code == status, r.text
    assert r.json()["error"]["message"]
    assert "skills-workspace" not in r.text      # FileExistsError carries a disk path
