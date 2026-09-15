"""Owner audit 2026-09-08, F4: the Coding page had no way to hand a task to
the agent. `/api/coding-tasks` is the owner-visible path to the ONE existing
OpenHands runtime (bossman.apprentice) — no second coding agent.

The sidecar here is a scripted Python process (the same contract the real
sidecar speaks); the sandbox, evidence derivation, scope boundary and cleanup
are the real ones. What this cannot prove is that a real model chooses
sensible edits — that stays the live acceptance's job.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import settings_kv
from bcc.features import coding_tasks as ct

pytest.importorskip("bossman.apprentice.openhands_client",
                    reason="bossman-core runtime not installed next to Command Center")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "owner-repo"
    src.mkdir()
    git(src, "init", "-q")
    git(src, "config", "user.email", "o@o")
    git(src, "config", "user.name", "owner")
    (src / "app").mkdir()
    (src / "app" / "main.py").write_text("print('v1')\n", encoding="utf-8")
    (src / "SECRETS.md").write_text("owner only\n", encoding="utf-8")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "init")
    return src


def sidecar(body: str) -> str:
    code = ("import json,sys,subprocess,pathlib,os; p=json.load(sys.stdin); "
            "w=pathlib.Path(p['workspace']); os.chdir(w); " + body + "; "
            "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed','summary':'done'}))")
    return json.dumps([sys.executable, "-c", code])[1:-1].replace('", "', '" "') if False else \
        " ".join(_q(x) for x in [sys.executable, "-c", code])


def _q(x: str) -> str:
    import shlex
    return shlex.quote(x)


async def _allow(env, root: Path):
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(
            key="code.roots", value_enc=env.svc.vault.encrypt(json.dumps([str(root)]))))
        await s.commit()


async def _wait_terminal(env, task_id, timeout=30.0):
    for _ in range(int(timeout * 10)):
        rec = (await env.client.get(f"/api/coding-tasks/{task_id}")).json()
        if rec["status"] in ct.TERMINAL:
            return rec
        await asyncio.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish: {rec}")


async def test_readiness_names_the_missing_sidecar_command(env, monkeypatch):
    monkeypatch.delenv(ct.COMMAND_ENV, raising=False)
    r = (await env.client.get("/api/coding-tasks/readiness")).json()
    assert r["available"] is False and r["runtime"] is True and r["sidecar_command"] is False
    assert ct.COMMAND_ENV in r["reason"]
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "x", "source_repo": "/tmp", "allowed_paths": ["a"]})
    assert res.status_code == 503 and res.json()["error"]["code"] == "OPENHANDS_UNAVAILABLE"


async def test_readiness_names_a_missing_runtime(env, monkeypatch):
    monkeypatch.setattr(ct, "RUNTIME_MODULE", "bossman.apprentice.__no_such_module__")
    monkeypatch.setenv(ct.COMMAND_ENV, "python -c pass")
    r = (await env.client.get("/api/coding-tasks/readiness")).json()
    assert r["available"] is False and r["runtime"] is False and "bossman-core" in r["reason"]


async def test_the_whole_owner_path_completes_with_real_evidence(env, repo, monkeypatch, tmp_path):
    monkeypatch.setenv(ct.COMMAND_ENV, sidecar(
        "pathlib.Path('app/main.py').write_text(\"print('v2')\\n\"); "
        "pathlib.Path('app/new.py').write_text('x = 1\\n')"))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)   # restored on teardown: gettempdir() must not stay pinned for later tests
    await _allow(env, repo.parent)
    ready = (await env.client.get("/api/coding-tasks/readiness")).json()
    assert ready["available"] is True
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "bump the version", "source_repo": str(repo),
        "allowed_paths": ["app"], "protected_paths": ["SECRETS.md"]})
    assert res.status_code == 200, res.text
    created = res.json()
    assert created["status"] == "running" and created["authority"] == {"push": False, "merge": False, "deploy": False}
    rec = await _wait_terminal(env, created["id"])
    assert rec["status"] == "completed", rec
    assert rec["changed_files"] == ["app/main.py", "app/new.py"]
    assert "+print('v2')" in rec["diff"] and "+x = 1" in rec["diff"]
    assert rec["sandbox_cleanup"]["removed"] is True
    assert rec["evidence"]["head_before"] == rec["evidence"]["head_after"]
    # the owner's repository is untouched: the patch is evidence, not an applied change
    assert (repo / "app" / "main.py").read_text() == "print('v1')\n"
    assert not (repo / "app" / "new.py").exists()
    listing = (await env.client.get("/api/coding-tasks")).json()["items"]
    assert listing[0]["id"] == created["id"] and "diff" not in listing[0] and listing[0]["diff_bytes"] > 0
    kinds = [e["kind"] for e in await env.svc.bus.recent(50)]
    assert "coding.task.created" in kinds and "coding.task.completed" in kinds


async def test_a_protected_file_edit_is_blocked_not_completed(env, repo, monkeypatch, tmp_path):
    monkeypatch.setenv(ct.COMMAND_ENV, sidecar("pathlib.Path('SECRETS.md').write_text('leak')"))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)   # restored on teardown: gettempdir() must not stay pinned for later tests
    await _allow(env, repo.parent)
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "x", "source_repo": str(repo), "allowed_paths": ["app"], "protected_paths": ["SECRETS.md"]})
    rec = await _wait_terminal(env, res.json()["id"])
    assert rec["status"] == "blocked" and "SECRETS.md" in rec["error"]
    assert (repo / "SECRETS.md").read_text() == "owner only\n"
    assert rec["sandbox_cleanup"]["removed"] is True


async def test_a_sidecar_that_reports_failure_is_failed(env, repo, monkeypatch, tmp_path):
    code = ("import json,sys; json.load(sys.stdin); "
            "print(json.dumps({'schema':'bossman.openhands.v1','status':'failed'}))")
    monkeypatch.setenv(ct.COMMAND_ENV, " ".join(_q(x) for x in [sys.executable, "-c", code]))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)   # restored on teardown: gettempdir() must not stay pinned for later tests
    await _allow(env, repo.parent)
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "x", "source_repo": str(repo), "allowed_paths": ["app"]})
    rec = await _wait_terminal(env, res.json()["id"])
    assert rec["status"] == "failed" and rec["changed_files"] == []


async def test_a_repository_outside_the_allowed_roots_is_refused(env, repo, monkeypatch, tmp_path):
    monkeypatch.setenv(ct.COMMAND_ENV, "python -c pass")
    other = tmp_path / "elsewhere"; other.mkdir()
    await _allow(env, other)
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "x", "source_repo": str(repo), "allowed_paths": ["app"]})
    assert res.status_code == 403


async def test_an_empty_allowlist_fails_closed(env, repo, monkeypatch):
    monkeypatch.setenv(ct.COMMAND_ENV, "python -c pass")
    await _allow(env, repo.parent)
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "x", "source_repo": str(repo), "allowed_paths": []})
    assert res.status_code == 422


def test_the_api_exposes_no_push_merge_or_deploy():
    paths = {r.path for r in ct.router.routes}
    assert not any(any(w in p for w in ("push", "merge", "deploy", "apply")) for p in paths), paths
