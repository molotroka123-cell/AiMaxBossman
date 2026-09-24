"""Coding path end to end through the product API with the LOCAL sidecar.

POST /api/coding-tasks → saved agent profile → IsolatedWorktree → OpenHandsClient
→ `python -m bossman.apprentice.local_sidecar` (real process) → the
DETERMINISTIC TEST MODEL (real HTTP server, labelled in the record) → tools on
real files → host-derived diff → Bossman's own `verify_tests` run → record.

Proves the plumbing and the verdict rules on real processes; says nothing about
a real model's skill (every record here carries deterministic_test_model=True).
"""
from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
import textwrap

import pytest
import sqlalchemy as sa

from bcc.db import agents as agents_t, settings_kv
from bcc.features import coding_tasks as ct

pytest.importorskip("bossman.apprentice.local_sidecar", reason="bossman-core runtime not installed")
from bossman.apprentice.scripted_model import MARKER, serve_in_thread  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "owner-repo"
    src.mkdir()
    git(src, "init", "-q")
    git(src, "config", "user.email", "o@o")
    git(src, "config", "user.name", "owner")
    (src / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (src / "test_calc.py").write_text(textwrap.dedent("""
        import unittest
        from calc import add
        class T(unittest.TestCase):
            def test_add(self):
                self.assertEqual(add(2, 3), 5)
        """), encoding="utf-8")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "init")
    return src


@pytest.fixture(autouse=True)
def _fresh():
    ct._handshake_cache.clear()
    yield
    ct._handshake_cache.clear()


def _use(monkeypatch, turns, name="e2e"):
    server, url = serve_in_thread({"name": name, "turns": turns})
    cmd = [sys.executable, "-m", "bossman.apprentice.local_sidecar", "--endpoint", url,
           "--model", f"{MARKER}-{name}"]
    monkeypatch.setenv(ct.COMMAND_ENV, " ".join(shlex.quote(x) for x in cmd))
    return server


async def _allow(env, root):
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(
            key="code.roots", value_enc=env.svc.vault.encrypt(json.dumps([str(root)]))))
        await s.commit()


async def _agent(env, **kw) -> int:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(agents_t).values(**kw))
        await s.commit()
        return int(res.inserted_primary_key[0])


async def _wait(env, task_id, timeout=90.0):
    rec = {}
    for _ in range(int(timeout * 10)):
        rec = (await env.client.get(f"/api/coding-tasks/{task_id}")).json()
        if rec["status"] in ct.TERMINAL:
            return rec
        await asyncio.sleep(0.1)
    raise AssertionError(f"not terminal: {rec}")


FIX = [
    {"tool": "read_file", "args": {"path": "calc.py"}},
    {"tool": "edit_file", "args": {"path": "calc.py", "old": "return a - b", "new": "return a + b"}},
    {"tool": "run_tests", "args": {"paths": ["test_calc.py"], "runner": "unittest"}},
    {"tool": "finish", "args": {"summary": "add fixed"}},
]


async def test_saved_agent_local_sidecar_and_host_verification_complete_the_task(env, repo, monkeypatch, tmp_path):
    server = _use(monkeypatch, FIX)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)
    try:
        await _allow(env, repo.parent)
        agent_id = await _agent(env, name="TOOL_FIRST", role="lab", system_prompt="Tools first.",
                                tools=["read_file", "edit_file", "run_tests", "finish"], max_steps=12,
                                permissions={"require_tests_before_finish": True, "use_memory": False})
        ready = (await env.client.get("/api/coding-tasks/readiness")).json()
        assert ready["available"] is True, json.dumps(ready)
        assert ready["handshake"]["executor"] == "bossman-local-sidecar"
        assert ready["handshake"]["deterministic_test_model"] is True
        res = await env.client.post("/api/coding-tasks", json={
            "instruction": "add() is wrong; fix it", "source_repo": str(repo), "allowed_paths": ["calc.py"],
            "agent_id": agent_id, "verify_tests": ["test_calc.py"], "timeout_seconds": 120})
        assert res.status_code == 200, res.text
        rec = await _wait(env, res.json()["id"])
    finally:
        server.shutdown()
    assert rec["status"] == "completed", json.dumps({k: rec.get(k) for k in ("error", "sidecar", "verification")}, ensure_ascii=False)[:3000]
    assert rec["agent"]["name"] == "TOOL_FIRST" and rec["sidecar"]["profile"] == "TOOL_FIRST"
    assert rec["sidecar"]["deterministic_test_model"] is True
    assert rec["changed_files"] == ["calc.py"] and "+    return a + b" in rec["diff"]
    assert rec["verification"]["passed"] is True and rec["verification"]["runner"] in ("unittest", "pytest")
    assert rec["memory"]["requested"] is False           # the agent's policy turned memory off
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"   # owner repo untouched
    assert rec["sandbox_cleanup"]["removed"] is True


async def test_host_verification_fails_the_task_even_when_the_sidecar_says_done(env, repo, monkeypatch, tmp_path):
    """The sidecar finishes WITHOUT fixing anything and claims success; Bossman's
    own run of the named tests turns that into failed (negative control for the
    completed verdict above)."""
    server = _use(monkeypatch, [{"tool": "finish", "args": {"summary": "all good, trust me"}}], name="liar")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)
    try:
        await _allow(env, repo.parent)
        res = await env.client.post("/api/coding-tasks", json={
            "instruction": "fix add", "source_repo": str(repo), "allowed_paths": ["calc.py"],
            "verify_tests": ["test_calc.py"], "timeout_seconds": 120, "use_memory": False})
        assert res.status_code == 200, res.text
        rec = await _wait(env, res.json()["id"])
    finally:
        server.shutdown()
    assert rec["sidecar"]["status"] == "completed"
    assert rec["status"] == "failed" and "независимая проверка" in rec["error"]
    assert rec["verification"]["passed"] is False


async def test_recipes_from_memory_reach_the_sidecar_and_their_check_is_enforced(env, repo, monkeypatch, tmp_path):
    """Executable recipes recalled for the task are forwarded as context and the
    sidecar refuses finish until the recipe's required check is green."""
    import types
    recipe = {"id": "R-calc-add", "symptom": "add returns a difference", "cause": "wrong operator",
              "action": "fix operator", "required_check": {"tool": "run_tests", "args": {"paths": ["test_calc.py"]}},
              "applies_when": "calc.add", "counterexample": "subtract()", "status": "VERIFIED"}

    async def executable_recipes(svc, instruction, project_id):
        return [recipe] if "add" in instruction else []

    monkeypatch.setitem(sys.modules, "bcc.features.coding_recipes",
                        types.SimpleNamespace(executable_recipes=executable_recipes))
    turns = [FIX[1], {"tool": "finish", "args": {"summary": "early"}}, FIX[2], FIX[3]]
    server = _use(monkeypatch, turns, name="recipe")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)
    try:
        await _allow(env, repo.parent)
        res = await env.client.post("/api/coding-tasks", json={
            "instruction": "fix add", "source_repo": str(repo), "allowed_paths": ["calc.py"],
            "timeout_seconds": 120})
        rec = await _wait(env, res.json()["id"])
    finally:
        server.shutdown()
    assert rec["status"] == "completed", json.dumps({k: rec.get(k) for k in ("error", "sidecar", "verification")}, ensure_ascii=False)[:3000]
    assert rec["memory"]["recipe_ids"] == ["R-calc-add"] and rec["memory"]["recalled"] is True
    assert rec["sidecar"]["recipes_applied"] == ["R-calc-add"] and rec["sidecar"]["memory_used"] is True
    assert ["finish", False] in [[c["tool"], c["ok"]] for c in rec["sidecar"]["tool_calls"]]


async def test_catalog_skills_reach_the_sidecar_for_a_bug_fix_task(env, repo, monkeypatch, tmp_path):
    """A bug-fix task gets the vetted methodology skills (debugging / TDD /
    verification) in the sidecar context; the record names them."""
    server = _use(monkeypatch, FIX, name="skills")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)
    try:
        await _allow(env, repo.parent)
        res = await env.client.post("/api/coding-tasks", json={
            "instruction": "Fix the bug: add() returns a wrong result; reproduce it with a failing test first",
            "source_repo": str(repo), "allowed_paths": ["calc.py"], "timeout_seconds": 120, "use_memory": False})
        rec = await _wait(env, res.json()["id"])
    finally:
        server.shutdown()
    assert rec["status"] == "completed", rec.get("error")
    ids = rec["skills"]["ids"]
    assert ids and any("debugging" in i for i in ids), rec["skills"]
    assert rec["sidecar"]["skills_used"] == ids[:3]


async def test_a_broken_skill_catalog_costs_guidance_not_the_task(env, repo, monkeypatch, tmp_path):
    from bcc.features import skills as skills_mod

    async def boom(*_a, **_k):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(skills_mod, "skills_for_task", boom)
    server = _use(monkeypatch, FIX, name="noskills")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)
    try:
        await _allow(env, repo.parent)
        res = await env.client.post("/api/coding-tasks", json={
            "instruction": "Fix the bug in add()", "source_repo": str(repo), "allowed_paths": ["calc.py"],
            "timeout_seconds": 120, "use_memory": False})
        rec = await _wait(env, res.json()["id"])
    finally:
        server.shutdown()
    assert rec["status"] == "completed" and rec["skills"]["ids"] == [] and rec["skills"]["error"] == "RuntimeError"


async def test_owner_cancel_kills_the_sidecar_tree_and_ends_cancelled(env, repo, monkeypatch, tmp_path):
    """STOP for a coding task: the student's hanging test run is killed with the
    sidecar (no orphan), the record ends failed/CANCELLED — never completed."""
    import time as _time
    marker = f"bossman-cancel-{_time.time_ns()}"
    (repo / "test_hang.py").write_text(
        f"import time, unittest\n# {marker}\nclass T(unittest.TestCase):\n"
        "    def test_hang(self):\n        time.sleep(300)\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "hang")
    server = _use(monkeypatch, [{"tool": "run_tests", "args": {"paths": ["test_hang.py"], "runner": "unittest"}},
                                {"tool": "finish", "args": {"summary": "x"}}], name="hang")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)
    try:
        await _allow(env, repo.parent)
        res = await env.client.post("/api/coding-tasks", json={
            "instruction": "run the tests", "source_repo": str(repo), "allowed_paths": ["calc.py"],
            "timeout_seconds": 600, "use_memory": False})
        task_id = res.json()["id"]
        import psutil
        def hanging():
            return [p for p in psutil.process_iter(["cmdline"])
                    if any("test_hang" in (a or "") for a in (p.info["cmdline"] or []))]
        # Waiting for the START only (slow under a loaded runner); the property
        # measured below — how fast a cancel ends the task — keeps its 30 s bound.
        for _ in range(1200):                                  # wait until the student's test is running
            if hanging():
                break
            await asyncio.sleep(0.1)
        assert hanging(), "the hanging test never started"
        t0 = _time.monotonic()
        c = (await env.client.post(f"/api/coding-tasks/{task_id}/cancel")).json()
        assert c["cancelled"] is True
        rec = await _wait(env, task_id, timeout=60)
        assert _time.monotonic() - t0 < 30
    finally:
        server.shutdown()
    assert rec["status"] == "failed" and rec["outcome"] == "CANCELLED", rec
    await asyncio.sleep(0.5)
    assert not hanging(), "the student's test process survived the cancel (orphan)"
    # negative control: cancelling a finished task changes nothing
    again = (await env.client.post(f"/api/coding-tasks/{task_id}/cancel")).json()
    assert again["cancelled"] is False and again["status"] == "failed"
