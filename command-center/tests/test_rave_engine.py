"""Agentic Rave (1.9, workstream G) — regression guards for the rave engine/API.

The owner-facing proof is the CMD transcripts (evidence/rc19/g); these tests
pin the contract: isolation per agent, pause/resume, STOP one/all (and the
owner global STOP), one agent's crash leaves the others running, conflicts
keep every version, restart recovery without duplicate execution, apply only
with an owner approval, subscription connectors behind opt-in + login checks.
CLI connectors run a stub with the official CLIs' interface (no network).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import client_for, make_settings, start_app

pytest.importorskip("bossman.apprentice.proc_tree", reason="bossman-core not importable")

STUB = Path(__file__).with_name("rave_stub_cli.py")


async def wait_for(c, rid, pred, timeout=30.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        v = (await c.get(f"/api/rave/{rid}")).json()
        if pred(v):
            return v
        if loop.time() > end:
            raise AssertionError(f"timeout; last state: {json.dumps(v, ensure_ascii=False)[:1500]}")
        await asyncio.sleep(0.05)


def agent(v, name):
    return next(a for a in v["agents"] if a["name"] == name)


def settled(v):
    return v["status"] != "running"


@pytest.fixture
async def app(tmp_path):
    app, svc = await start_app(make_settings(tmp_path))
    async with client_for(app, svc) as c:
        yield c, svc
    await svc.stop()


async def start(c, agents, prompt="build the thing", **extra):
    r = await c.post("/api/rave", json={"prompt": prompt, "agents": agents, **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


async def test_two_agents_isolated_workspaces_and_untouched_base(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=2&delay=0", "mock:b?steps=3&delay=0"])
    v = await wait_for(c, rid, settled)
    assert v["status"] == "done"
    a, b = agent(v, "a"), agent(v, "b")
    assert [f["path"] for f in a["changed_files"]] == ["rave/a.md"]
    assert [f["path"] for f in b["changed_files"]] == ["rave/b.md"]
    assert a["workspace"] != b["workspace"]
    assert not (Path(a["workspace"]) / "rave" / "b.md").exists()   # no cross-talk
    assert not (Path(b["workspace"]) / "rave" / "a.md").exists()
    base = Path(v["repo"])
    assert git(base, "rev-parse", "HEAD") == v["base_commit"]
    assert git(base, "status", "--porcelain") == ""                 # the project is untouched
    assert git(a["workspace"], "remote") == ""                        # no way back to the source
    assert a["auth"] == "none" and a["provider"].startswith("mock")
    diff = (await c.get(f"/api/rave/{rid}/agents/a/diff")).json()
    assert "rave/a.md" in diff["patch"] and "+- step 2/2 by a" in diff["patch"]


async def test_crash_of_one_agent_leaves_the_others_running(app):
    c, _ = app
    rid = await start(c, ["mock:ok?steps=3&delay=0.05", "mock:bad?crash_at=2&delay=0"])
    v = await wait_for(c, rid, settled)
    assert agent(v, "ok")["status"] == "done"
    bad = agent(v, "bad")
    assert bad["status"] == "failed" and "crashed at step 2" in bad["error"]
    assert bad["result_commit"]                                   # partial work kept as a commit
    assert v["status"] == "partial"


async def test_same_file_conflict_is_reported_and_every_version_kept(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=1&delay=0&file=README.md", "mock:b?steps=2&delay=0&file=README.md"])
    v = await wait_for(c, rid, settled)
    (conf,) = v["conflicts"]
    assert conf["file"] == "README.md" and conf["agents"] == ["a", "b"]
    assert conf["kind"] == "both_modified" and conf["auto_mergeable"] is False
    slot = Path(conf["artifacts"])
    names = sorted(p.name for p in slot.iterdir())
    assert names == ["a__README.md", "b__README.md", "base__README.md", "merged__README.md"]
    assert "by a" in (slot / "a__README.md").read_text() and "by b" in (slot / "b__README.md").read_text()
    assert "<<<<<<< a" in (slot / "merged__README.md").read_text()
    # and both agents still have their own version in their workspace
    assert "by a" in (Path(agent(v, "a")["workspace"]) / "README.md").read_text()
    assert "by b" in (Path(agent(v, "b")["workspace"]) / "README.md").read_text()


async def test_pause_and_resume_one_agent(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=8&delay=0.15", "mock:b?steps=2&delay=0.05"])
    await wait_for(c, rid, lambda v: agent(v, "a")["step"] >= 2)
    r = (await c.post(f"/api/rave/{rid}/pause", json={"agent": "a"})).json()
    assert r["changed"] == ["a"]
    v = await wait_for(c, rid, lambda v: agent(v, "a")["status"] == "paused")
    step = agent(v, "a")["step"]
    await asyncio.sleep(0.6)
    v = (await c.get(f"/api/rave/{rid}")).json()
    assert agent(v, "a")["step"] == step and agent(v, "a")["status"] == "paused"
    assert agent(v, "b")["status"] == "done"                     # the other agent was not paused
    await c.post(f"/api/rave/{rid}/resume", json={"agent": "a"})
    v = await wait_for(c, rid, settled)
    assert agent(v, "a")["status"] == "done"


async def test_stop_one_then_stop_all(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=40&delay=0.1", "mock:b?steps=40&delay=0.1", "mock:c?steps=40&delay=0.1"])
    await wait_for(c, rid, lambda v: agent(v, "a")["step"] >= 2)
    r = (await c.post(f"/api/rave/{rid}/stop", json={"agent": "a"})).json()
    assert r["changed"] == ["a"]
    v = (await c.get(f"/api/rave/{rid}")).json()
    assert agent(v, "a")["status"] == "stopped" and agent(v, "b")["status"] == "running"
    assert agent(v, "a")["result_commit"]                          # partial work preserved
    await c.post(f"/api/rave/{rid}/stop", json={})
    v = await wait_for(c, rid, settled)
    assert {a["status"] for a in v["agents"]} == {"stopped"}
    assert v["status"] == "stopped"


async def test_owner_global_stop_stops_every_rave(app):
    c, svc = app
    r1 = await start(c, ["mock:a?steps=40&delay=0.1"])
    r2 = await start(c, ["mock:b?steps=40&delay=0.1"])
    await wait_for(c, r2, lambda v: agent(v, "b")["step"] >= 1)
    await svc.bus.emit("owner.stop_all", ok=True)
    for rid in (r1, r2):
        v = await wait_for(c, rid, settled)
        assert v["status"] == "stopped"


async def test_restart_recovers_paused_and_never_duplicates_steps(tmp_path, monkeypatch):
    from bcc.rave import engine
    settings = make_settings(tmp_path)
    app1, svc1 = await start_app(settings)
    async with client_for(app1, svc1) as c:
        rid = await start(c, ["mock:a?steps=6&delay=0.2", "mock:quick?steps=1&delay=0"])
        await wait_for(c, rid, lambda v: agent(v, "a")["step"] >= 3 and agent(v, "quick")["status"] == "done")
    await svc1.stop()                                    # backend goes away mid-rave
    monkeypatch.setattr(engine, "BOOT_ID", "second-boot")
    app2, svc2 = await start_app(settings)
    try:
        async with client_for(app2, svc2) as c:
            v = (await c.get(f"/api/rave/{rid}")).json()
            a = agent(v, "a")
            assert a["status"] == "paused" and a["pause_reason"] == "recovered_after_restart"
            assert agent(v, "quick")["status"] == "done"
            assert v["status"] == "paused"
            r = (await c.post(f"/api/rave/{rid}/resume", json={})).json()
            assert r["changed"] == ["a"]                   # the finished agent is not re-run
            v = await wait_for(c, rid, settled)
            assert agent(v, "a")["status"] == "done"
            log = (Path(agent(v, "a")["workspace"]).parent / "exec.log").read_text().splitlines()
            for k in range(1, 7):
                runs = [ln for ln in log if f"step {k} executed" in ln or f"step {k} reconciled" in ln]
                assert len([ln for ln in runs if "executed" in ln]) == 1, log
            assert any("second-boot" in ln for ln in log)
            quick_log = (Path(agent(v, "quick")["workspace"]).parent / "exec.log").read_text().splitlines()
            assert len(quick_log) == 1
    finally:
        await svc2.stop()


async def test_apply_needs_owner_approval_and_refuses_conflicting_second(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=1&delay=0&file=README.md", "mock:b?steps=1&delay=0&file=README.md"])
    v = await wait_for(c, rid, settled)
    r = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert r.status_code == 202 and r.json()["state"] == "WAIT_APPROVAL"
    aid = r.json()["approval_id"]
    base = Path(v["repo"])
    assert "by a" not in (base / "README.md").read_text()        # nothing written without approval
    bad = await c.post(f"/api/rave/{rid}/agents/a/apply", json={"approval_id": aid})
    assert bad.status_code == 403                                  # pending is not approved
    await c.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner:test"})
    ok = await c.post(f"/api/rave/{rid}/agents/a/apply", json={"approval_id": aid})
    assert ok.status_code == 200 and ok.json()["files"] == ["README.md"]
    assert "by a" in (base / "README.md").read_text()
    again = await c.post(f"/api/rave/{rid}/agents/a/apply", json={"approval_id": aid})
    assert again.status_code == 409                                # already applied / no replay
    conflict = await c.post(f"/api/rave/{rid}/agents/b/apply", json={})
    body = conflict.json()
    assert conflict.status_code == 409 and (body.get("detail") or body.get("error"))["code"] == "CONFLICT", body
    assert "by a" in (base / "README.md").read_text()              # b did not overwrite a
    assert "by b" in (Path(agent(v, "b")["workspace"]) / "README.md").read_text()
    pend = (await c.get("/api/approvals", params={"status": "pending"})).json()
    assert not [p for p in pend if p["kind"] == "rave_apply"]      # no approval asked for a doomed apply


async def test_owner_test_command_runs_in_each_workspace(app, tmp_path):
    c, _ = app
    script = tmp_path / "check.py"
    script.write_text("import os, sys\nsys.exit(0 if os.path.exists(os.path.join('rave', 'a.md')) else 1)\n")
    rid = await start(c, ["mock:a?steps=1&delay=0", "mock:b?steps=1&delay=0"],
                      test=f'"{sys.executable}" "{script}"')
    v = await wait_for(c, rid, settled)
    assert agent(v, "a")["tests"]["passed"] is True
    assert agent(v, "b")["tests"]["passed"] is False and agent(v, "b")["tests"]["exit_code"] == 1


async def test_bad_input_is_refused(app):
    c, _ = app
    for agents in (["nope:a"], ["mock:a", "mock:a"], ["mock:../x"], ["mock:a?file=../escape.txt"]):
        r = await c.post("/api/rave", json={"prompt": "x", "agents": agents})
        assert r.status_code == 422, (agents, r.text)
    r = await c.post("/api/rave", json={"prompt": "x", "agents": ["mock:a"], "repo": "C:/Windows"})
    assert r.status_code in (400, 403)
    assert (await c.get("/api/rave/rv-zzzzzzzz")).status_code == 404
    assert (await c.get("/api/rave/..%2F..")).status_code == 404


async def test_refused_repo_leaves_no_orphan_rave_directory(app):
    """A create() refused for its repo must not leave a half-made rv-* directory."""
    c, svc = app
    root = Path(svc.settings.data_dir) / "rave"
    before = {p.name for p in root.glob("rv-*")}
    r = await c.post("/api/rave", json={"prompt": "x", "agents": ["mock:a"], "repo": "C:/Windows"})
    assert r.status_code in (400, 403)
    assert {p.name for p in root.glob("rv-*")} == before


# ------------------------------------------------------------------ subscription CLIs (stub)


def use_stub(monkeypatch, login="subscription", **env):
    monkeypatch.setenv("BOSSMAN_RAVE_CLAUDE_CMD", json.dumps([sys.executable, str(STUB), "claude"]))
    monkeypatch.setenv("BOSSMAN_RAVE_CODEX_CMD", json.dumps([sys.executable, str(STUB), "codex"]))
    monkeypatch.setenv("STUB_LOGIN", login)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


async def test_claude_needs_owner_optin_then_runs_under_subscription(app, monkeypatch):
    c, _ = app
    use_stub(monkeypatch)
    rid = await start(c, ["claude:cc", "mock:m?steps=1&delay=0"], prompt="say hi")
    v = await wait_for(c, rid, settled)
    cc = agent(v, "cc")
    assert cc["status"] == "blocked" and "bossman approve" in cc["error"]
    assert cc["auth"] == "subscription (claude login)"
    assert agent(v, "m")["status"] == "done"                       # the rest of the rave ran
    aid = cc["meta"]["approval_id"]
    await c.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner:test"})
    await c.post(f"/api/rave/{rid}/resume", json={"agent": "cc"})
    v = await wait_for(c, rid, lambda v: agent(v, "cc")["status"] not in ("blocked", "running", "queued")
                       and not agent(v, "cc").get("finalizing"))
    cc = agent(v, "cc")
    assert cc["status"] == "done", cc
    assert cc["answer"].startswith("STUB-CLAUDE-OK")
    assert [f["path"] for f in cc["changed_files"]] == ["CLAUDE_STUB.md"]
    # the opt-in is remembered: a new rave does not ask again
    rid2 = await start(c, ["claude:c2"], prompt="again")
    v = await wait_for(c, rid2, settled)
    assert agent(v, "c2")["status"] == "done"


async def test_cli_not_logged_in_shows_the_manual_login_step(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, login="none")
    (svc.rave.root / "optin.json").write_text(json.dumps({"claude": {"approved": True}, "codex": {"approved": True}}))
    rid = await start(c, ["claude:cc", "codex:cx"])
    v = await wait_for(c, rid, settled)
    assert agent(v, "cc")["status"] == "blocked" and "claude auth login" in agent(v, "cc")["error"]
    assert agent(v, "cx")["status"] == "blocked" and "codex login" in agent(v, "cx")["error"]


async def test_api_key_login_is_off_by_default(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, login="apikey")
    monkeypatch.delenv("BOSSMAN_RAVE_ALLOW_API_KEY", raising=False)
    (svc.rave.root / "optin.json").write_text(json.dumps({"claude": {"approved": True}, "codex": {"approved": True}}))
    rid = await start(c, ["claude:cc", "codex:cx?auth=api_key"])
    v = await wait_for(c, rid, settled)
    for name in ("cc", "cx"):
        assert agent(v, name)["status"] == "blocked"
        assert "не по подписке" in agent(v, name)["error"]


async def test_codex_runs_under_chatgpt_subscription_after_optin(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch)
    (svc.rave.root / "optin.json").write_text(json.dumps({"codex": {"approved": True}}))
    rid = await start(c, ["codex:cx"], prompt="add a note")
    v = await wait_for(c, rid, settled)
    cx = agent(v, "cx")
    assert cx["status"] == "done" and cx["answer"].startswith("STUB-CODEX-OK")
    assert cx["auth"] == "subscription (codex login)"
    assert [f["path"] for f in cx["changed_files"]] == ["CODEX_STUB.md"]


async def test_connectors_status_keeps_no_identity(app, monkeypatch):
    c, _ = app
    use_stub(monkeypatch)
    data = (await c.get("/api/rave/connectors")).json()
    assert data["claude"]["auth"] == "subscription (claude login)" and data["claude"]["plan"] == "max"
    assert data["codex"]["auth"] == "subscription (codex login)"
    assert "owner@example.invalid" not in json.dumps(data)
    assert data["api_key_path"]["enabled"] is False


async def test_agent_that_tampers_with_its_git_is_not_trusted(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, STUB_TAMPER="1")
    (svc.rave.root / "optin.json").write_text(json.dumps({"claude": {"approved": True}}))
    rid = await start(c, ["claude:evil"])
    v = await wait_for(c, rid, settled)
    ev = agent(v, "evil")
    assert ev["status"] == "blocked" and ".git" in ev["error"]
    assert not ev["result_commit"]                                  # git was not run in that workspace
    copy = Path(ev["workspace"]).parent / "files-copy" / "CLAUDE_STUB.md"
    assert copy.is_file()                                           # its files are still preserved


async def test_cli_process_is_killed_on_stop(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, STUB_SLEEP="30")
    (svc.rave.root / "optin.json").write_text(json.dumps({"claude": {"approved": True}}))
    rid = await start(c, ["claude:slow"])
    await wait_for(c, rid, lambda v: agent(v, "slow")["step"] == 1)
    await asyncio.sleep(0.5)
    r = (await c.post(f"/api/rave/{rid}/pause", json={"agent": "slow"})).json()
    assert agent(r["rave"], "slow")["status"] == "paused"            # child process suspended at once
    t0 = asyncio.get_running_loop().time()
    await c.post(f"/api/rave/{rid}/stop", json={})
    v = await wait_for(c, rid, settled, timeout=20)
    assert agent(v, "slow")["status"] == "stopped"
    assert asyncio.get_running_loop().time() - t0 < 15               # not waiting out the 30 s child


async def test_concurrent_stops_and_pause_never_leave_an_agent_half_stopped(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=40&delay=0.1", "mock:b?steps=40&delay=0.1"])
    await wait_for(c, rid, lambda v: agent(v, "a")["step"] >= 1)
    await asyncio.gather(c.post(f"/api/rave/{rid}/stop", json={"agent": "a"}),
                         c.post(f"/api/rave/{rid}/stop", json={}),
                         c.post(f"/api/rave/{rid}/pause", json={}),
                         c.post(f"/api/rave/{rid}/stop", json={"agent": "a"}))
    v = await wait_for(c, rid, settled)
    assert {x["status"] for x in v["agents"]} == {"stopped"}, v
    assert not any(x.get("finalizing") for x in v["agents"])
