"""Agentic Rave — the owner-facing flow (night 2026-09-30, lane rave-apps).

Pins, each red on the previous code: the Claude Code CLI version preflight (`--permission-prompts none`
needs >= 2.1.259) and a stub CLI that rejects unknown flags; the awaitable, CONFIRMED global STOP
(`RaveService.stop_all()` -> counts); approval lookups that do not stop at the newest 100 rows; CLI exit
code 3 when the backend is unreachable; `rave prune`; the connectors endpoint with CLI versions.
The CLI connectors run the stub in tests/rave_stub_cli.py (same interface as the official CLIs, no network).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bcc.rave import cli as rave_cli
from bcc.rave import connectors
from bcc.rave.engine import OPTIN_PREVIEW, AgentCtx, Runner, _suspend
from bcc.terminal_cli.api_client import BossmanError

from .conftest import client_for, make_settings, start_app

pytest.importorskip("bossman.apprentice.proc_tree", reason="bossman-core not importable")

STUB = Path(__file__).with_name("rave_stub_cli.py")
DAY = 86400


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


@pytest.fixture(autouse=True)
def _fresh_version_cache():
    connectors.reset_version_cache()
    yield
    connectors.reset_version_cache()


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


def use_stub(monkeypatch, login="subscription", **env):
    monkeypatch.setenv("BOSSMAN_RAVE_CLAUDE_CMD", json.dumps([sys.executable, str(STUB), "claude"]))
    monkeypatch.setenv("BOSSMAN_RAVE_CODEX_CMD", json.dumps([sys.executable, str(STUB), "codex"]))
    monkeypatch.setenv("STUB_LOGIN", login)
    monkeypatch.delenv("STUB_VERSION", raising=False)
    monkeypatch.delenv("STUB_LIMIT", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def approve_optin(svc, *keys):
    (svc.rave.root / "optin.json").write_text(json.dumps({k: {"approved": True} for k in keys}))


def run_stub(tool, *argv, cwd, env=None, stdin=""):
    return subprocess.run([sys.executable, str(STUB), tool, *argv], input=stdin, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=60, cwd=cwd)


def code_of(resp):
    body = resp.json()
    return (body.get("detail") or body.get("error"))["code"]


# ------------------------------------------------------------------ (a) Claude CLI version preflight


async def test_old_claude_cli_blocks_the_agent_with_an_update_message(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, STUB_VERSION="2.1.258")
    approve_optin(svc, "claude")
    rid = await start(c, ["claude:cc", "mock:m?steps=1&delay=0"])
    v = await wait_for(c, rid, settled)
    cc = agent(v, "cc")
    assert cc["status"] == "blocked", cc
    assert "обновите Claude Code CLI до 2.1.259+" in cc["error"] and "2.1.258" in cc["error"]
    assert cc["meta"]["version"] == "2.1.258"
    assert not cc["changed_files"]                                   # nothing ran
    assert agent(v, "m")["status"] == "done"                          # the rest of the rave went on


async def test_unparseable_claude_version_is_blocked_with_the_same_advice(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, STUB_VERSION="garbage")
    approve_optin(svc, "claude")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "blocked" and "не удалось определить версию" in cc["error"]
    assert "2.1.259+" in cc["error"]


async def test_the_minimum_version_itself_runs_and_the_flag_is_never_dropped(app, monkeypatch):
    """Negative control of the two tests above: exactly 2.1.259 is enough. The stub accepts
    `--permission-prompts` only from that version, so a connector that dropped or misspelled the flag
    would also fail here."""
    c, svc = app
    use_stub(monkeypatch, STUB_VERSION="2.1.259")
    approve_optin(svc, "claude")
    rid = await start(c, ["claude:cc"], prompt="say hi")
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and cc["answer"].startswith("STUB-CLAUDE-OK")


async def test_claude_version_is_asked_once_and_cached(monkeypatch):
    use_stub(monkeypatch)
    calls = []
    real = connectors._status_proc

    async def counting(argv, env, timeout=45):
        calls.append(argv[-1])
        return await real(argv, env, timeout)

    monkeypatch.setattr(connectors, "_status_proc", counting)
    first = await connectors.claude_version()
    second = await connectors.claude_version()
    assert first == second and first["ok"] is True and first["version"] == "2.1.284"
    assert calls == ["--version"]                                      # the second answer came from the cache
    connectors.reset_version_cache()
    await connectors.claude_version()
    assert calls == ["--version", "--version"]


def test_version_parsing():
    assert connectors.parse_version("2.1.284 (Claude Code)") == (2, 1, 284)
    assert connectors.parse_version("codex-cli 0.157.1") == (0, 157, 1)
    assert connectors.parse_version("no version here") is None
    assert connectors.parse_version("") is None
    assert (2, 1, 258) < connectors.MIN_CLAUDE_VERSION <= (2, 1, 259)


def test_stub_cli_rejects_unknown_flags_like_the_real_cli(tmp_path):
    bad = run_stub("claude", "-p", "--bogus-flag", cwd=tmp_path)
    assert bad.returncode == 1 and "unknown option '--bogus-flag'" in bad.stderr
    old = run_stub("claude", "-p", "--permission-prompts", "none", cwd=tmp_path, env={"STUB_VERSION": "2.1.200"})
    assert old.returncode == 1 and "unknown option '--permission-prompts'" in old.stderr
    bad_choice = run_stub("claude", "-p", "--permission-prompts", "maybe", cwd=tmp_path)
    assert bad_choice.returncode == 1 and "invalid" in bad_choice.stderr
    missing = run_stub("claude", "-p", "--model", cwd=tmp_path)
    assert missing.returncode == 1 and "argument missing" in missing.stderr
    codex = run_stub("codex", "exec", "--not-a-codex-flag", "-", cwd=tmp_path)
    assert codex.returncode == 1 and "unknown option" in codex.stderr
    # negative control: the flags the connectors really use are accepted
    ok = run_stub("claude", "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
                  "--permission-prompts", "none", "--no-session-persistence", stdin="hi", cwd=tmp_path,
                  env={"STUB_VERSION": "2.1.259"})
    assert ok.returncode == 0 and "STUB-CLAUDE-OK" in ok.stdout


async def test_missing_bossman_core_blocks_cli_agents_with_a_clear_message(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch)
    approve_optin(svc, "claude", "codex")
    monkeypatch.setattr(connectors, "_core_path", lambda: None)
    rid = await start(c, ["claude:cc", "codex:cx"])
    v = await wait_for(c, rid, settled)
    for name in ("cc", "cx"):
        assert agent(v, name)["status"] == "blocked" and "bossman-core" in agent(v, name)["error"]


async def test_connectors_endpoint_reports_cli_versions_and_the_minimum(app, monkeypatch):
    c, _ = app
    use_stub(monkeypatch)
    data = (await c.get("/api/rave/connectors", params={"refresh": "1"})).json()
    assert data["claude"]["version"] == "2.1.284" and data["claude"]["version_ok"] is True
    assert data["claude"]["version_min"] == "2.1.259" and data["claude"]["version_problem"] == ""
    assert data["codex"]["version"] == "0.157.1"
    assert "owner@example.invalid" not in json.dumps(data)            # still no identity
    monkeypatch.setenv("STUB_VERSION", "2.1.200")
    connectors.reset_version_cache()
    old = (await c.get("/api/rave/connectors", params={"refresh": "1"})).json()["claude"]
    assert old["version"] == "2.1.200" and old["version_ok"] is False and "2.1.259+" in old["version_problem"]


async def test_connectors_endpoint_is_cached_for_a_burst_and_refreshable(app, monkeypatch):
    c, _ = app
    use_stub(monkeypatch)
    calls = []
    real = connectors.claude_login

    async def counting(**kw):
        calls.append(kw)
        return await real(**kw)

    monkeypatch.setattr(connectors, "claude_login", counting)
    await c.get("/api/rave/connectors", params={"refresh": "1"})
    await c.get("/api/rave/connectors")
    await c.get("/api/rave/connectors")
    assert len(calls) == 1                                             # the page re-renders often: one status call
    await c.get("/api/rave/connectors", params={"refresh": "1"})
    assert len(calls) == 2


def test_limit_detection_reads_failed_runs_only_by_their_text():
    at = time.time() + 3600
    hit = connectors.detect_limit(f"Claude AI usage limit reached|{int(at)}")
    assert hit and hit["reset_at"] == float(int(at))
    for text in ("You've hit your limit · resets 3pm", "You've hit your usage limit. Try again later.",
                 "API Error: 429 Too Many Requests", "5-hour limit reached"):
        assert connectors.detect_limit(text), text
    assert connectors.detect_limit("Claude AI usage limit reached|123")["reset_at"] is None   # nonsense epoch
    for text in ("boom", "Permission denied", "claude -p failed (exit 3): boom", "Could not resolve host", ""):
        assert connectors.detect_limit(text) is None, text


# ------------------------------------------------------------------ (b) global STOP is awaitable and confirmed


async def test_stop_all_is_awaitable_and_confirms_with_counts(app):
    c, svc = app
    r1 = await start(c, ["mock:a?steps=40&delay=0.1", "mock:b?steps=40&delay=0.1"])
    r2 = await start(c, ["mock:c?steps=40&delay=0.1"])
    await wait_for(c, r1, lambda v: agent(v, "a")["step"] >= 1)
    await wait_for(c, r2, lambda v: agent(v, "c")["step"] >= 1)
    assert sorted(svc.rave.active_agents()) == sorted([f"{r1}/a", f"{r1}/b", f"{r2}/c"])
    res = await svc.rave.stop_all()
    assert res["ok"] is True and res["remaining"] == 0 and res["remaining_agents"] == [] and res["errors"] == []
    assert res["stopped_count"] == 3
    assert {k: sorted(v) for k, v in res["stopped"].items()} == {r1: ["a", "b"], r2: ["c"]}
    # confirmed means confirmed: right after the await nothing is left to wait for
    assert svc.rave.active_agents() == []
    for rid in (r1, r2):
        v = (await c.get(f"/api/rave/{rid}")).json()
        assert v["status"] == "stopped" and {a["status"] for a in v["agents"]} == {"stopped"}
    again = await svc.rave.stop_all()                                  # nothing running: a clean, empty answer
    assert again["ok"] is True and again["stopped"] == {} and again["remaining"] == 0


async def test_stop_all_reports_what_it_could_not_stop(app, monkeypatch):
    c, svc = app
    rid = await start(c, ["mock:a?steps=40&delay=0.1"])
    await wait_for(c, rid, lambda v: agent(v, "a")["step"] >= 1)
    real_stop = svc.rave.stop

    async def stuck(rid_, agent_=None):
        return {"ok": True, "changed": [], "rave": svc.rave.view(svc.rave.load(rid_))}

    monkeypatch.setattr(svc.rave, "stop", stuck)
    res = await svc.rave.stop_all()
    assert res["ok"] is False and res["remaining"] == 1 and res["remaining_agents"] == [f"{rid}/a"]

    async def boom(rid_, agent_=None):
        raise RuntimeError("cannot stop")

    monkeypatch.setattr(svc.rave, "stop", boom)
    res = await svc.rave.stop_all()
    assert res["ok"] is False and res["errors"][0]["rave"] == rid and "cannot stop" in res["errors"][0]["error"]
    monkeypatch.setattr(svc.rave, "stop", real_stop)
    assert (await svc.rave.stop_all())["ok"] is True


async def test_stop_all_http_answer_has_the_same_counts(app):
    c, _ = app
    rid = await start(c, ["mock:a?steps=40&delay=0.1"])
    await wait_for(c, rid, lambda v: agent(v, "a")["step"] >= 1)
    res = (await c.post("/api/rave/stop-all")).json()
    assert res["ok"] is True and res["stopped_count"] == 1 and res["remaining"] == 0 and res["stopped"] == {rid: ["a"]}


def test_cli_stop_all_never_prints_a_silent_ok_when_unconfirmed(capsys):
    class Client:
        def post(self, path, body=None, **kw):
            return {"ok": False, "stopped": {}, "remaining": 1, "remaining_agents": ["rv-12345678/a"], "errors": []}

    args = rave_cli.build_parser().parse_args(["stop", "--all"])
    assert rave_cli.run(Client(), args, rave_cli.Printer(False)) == rave_cli.EXIT_FAIL
    out = capsys.readouterr().out
    assert "НЕ ПОДТВЕРЖДЕНО" in out and "rv-12345678/a" in out


# ------------------------------------------------------------------ (d) paused time and the timeout


async def test_paused_time_does_not_count_against_the_agent_timeout(tmp_path):
    """Decision (the design doc said the opposite, the code is right): a long pause must not eat the agent's
    timeout, otherwise a resumed agent would be killed at once."""
    runner = Runner("rv-00000000", "a")
    runner.gate.clear()                                                # paused before the process starts
    argv = [sys.executable, "-c", "import time; time.sleep(1.2); print('finished')"]
    task = asyncio.create_task(AgentCtx.spawn(None, runner, argv, cwd=tmp_path, stdin=b"", timeout=1, env=None))
    await asyncio.sleep(3.5)                                           # suspended for 3.5 s > the 1 s timeout
    assert not task.done() and runner.suspended
    pid = runner.tree.pid
    runner.gate.set()
    await asyncio.to_thread(_suspend, pid, False)
    runner.suspended = False
    res = await asyncio.wait_for(task, 30)
    assert res.timed_out is False and b"finished" in res.stdout


async def test_running_time_still_counts_against_the_timeout(tmp_path):
    """Negative control: the same child NOT paused is killed at the timeout."""
    runner = Runner("rv-00000000", "a")
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    t0 = time.monotonic()
    res = await AgentCtx.spawn(None, runner, argv, cwd=tmp_path, stdin=b"", timeout=1, env=None)
    assert res.timed_out is True and time.monotonic() - t0 < 15


# ------------------------------------------------------------------ (e) approval lookups beyond the newest 100


async def test_connector_optin_is_found_behind_a_busy_approvals_queue(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch)
    opt = await svc.approvals.create("rave_connector_optin", OPTIN_PREVIEW["claude"])
    await svc.approvals.decide(opt["id"], True, "owner:test")
    for i in range(105):                                               # newer rows push the opt-in past limit=100
        await svc.approvals.create("noise", f"noise {i}")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done", cc                                  # the old approval was found, not asked again
    pend = (await c.get("/api/approvals", params={"status": "pending"})).json()
    assert not [p for p in pend if p["kind"] == "rave_connector_optin"]   # and no duplicate was created


async def test_pending_apply_request_is_reused_behind_a_busy_approvals_queue(app):
    c, svc = app
    rid = await start(c, ["mock:a?steps=1&delay=0&file=README.md"])
    await wait_for(c, rid, settled)
    first = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert first.status_code == 202
    for i in range(105):
        await svc.approvals.create("noise", f"noise {i}")
    second = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert second.status_code == 202 and second.json()["approval_id"] == first.json()["approval_id"]


# ------------------------------------------------------------------ (f) CLI exit code 3 on a lost connection


def test_cli_exits_3_when_the_backend_is_unreachable(monkeypatch, capsys):
    from bcc.terminal_cli import api_client

    def unreachable(*a, **k):
        raise BossmanError("Bossman не отвечает", kind="disconnected", hint="`bossman status`")

    monkeypatch.setattr(api_client, "discover", unreachable)
    assert rave_cli.main(["list"]) == 3
    assert "Bossman не отвечает" in capsys.readouterr().out


def test_cli_exit_codes_for_failures_of_a_running_command():
    class Client:
        def __init__(self, kind):
            self.kind = kind

        def get(self, path, **kw):
            raise BossmanError("x", kind=self.kind)

    args = rave_cli.build_parser().parse_args(["list"])
    pr = rave_cli.Printer(False)
    assert rave_cli.run(Client("disconnected"), args, pr) == 3          # went away mid-command
    assert rave_cli.run(Client("auth"), args, pr) == 3                  # token refused
    assert rave_cli.run(Client("conflict"), args, pr) == rave_cli.EXIT_CONFLICT
    assert rave_cli.run(Client("api"), args, pr) == rave_cli.EXIT_FAIL  # negative control: other errors stay 1


# ------------------------------------------------------------------ (g) prune


async def finished_rave(c, agents, **extra):
    rid = await start(c, agents, **extra)
    return rid, await wait_for(c, rid, settled)


def age(svc, rid, days):
    """Make a finished rave look `days` old (its records only; nothing is running)."""
    path = svc.rave.root / rid / "rave.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    old = time.time() - days * DAY
    rec["created_at"] = rec["updated_at"] = old
    for a in rec["agents"]:
        a["started_at"] = a["finished_at"] = old
    for entry in (rec.get("applied") or {}).values():
        entry["at"] = old
    path.write_text(json.dumps(rec), encoding="utf-8")


async def test_prune_is_a_dry_run_by_default_and_spares_running_raves(app):
    c, svc = app
    old_rid, v = await finished_rave(c, ["mock:a?steps=1&delay=0"])
    live_rid = await start(c, ["mock:b?steps=40&delay=0.1"])
    await wait_for(c, live_rid, lambda x: agent(x, "b")["step"] >= 1)
    age(svc, old_rid, 10)
    ws = Path(agent(v, "a")["workspace"])
    live_ws = Path(agent(await wait_for(c, live_rid, lambda x: True), "b")["workspace"])
    assert ws.is_dir() and live_ws.is_dir()

    dry = (await c.post("/api/rave/prune", json={"older_than_days": 7})).json()
    assert dry["dry_run"] is True and [i["id"] for i in dry["items"]] == [old_rid]
    assert dry["items"][0]["bytes"] > 0 and dry["freed_bytes"] == dry["items"][0]["bytes"]
    assert ws.is_dir() and (svc.rave.root / old_rid / "base").is_dir()    # a dry run deletes nothing

    # even at 0 days (everything is old enough) a rave with a live agent is never touched
    res = (await c.post("/api/rave/prune", json={"older_than_days": 0, "dry_run": False})).json()
    assert [i["id"] for i in res["items"]] == [old_rid]
    assert {"id": live_rid, "reason": "рейв ещё работает"} in res["skipped"]
    assert not ws.exists() and not (svc.rave.root / old_rid / "base").exists()
    assert live_ws.is_dir()                                            # the running rave keeps its workspace
    assert (await c.get(f"/api/rave/{live_rid}")).json()["status"] == "running"
    assert (svc.rave.root / old_rid / "rave.json").is_file() and (svc.rave.root / old_rid / "events.jsonl").is_file()
    await c.post(f"/api/rave/{live_rid}/stop", json={})


async def test_prune_leaves_recent_raves_and_does_not_list_a_pruned_one_twice(app):
    c, svc = app
    recent, _ = await finished_rave(c, ["mock:a?steps=1&delay=0"])
    stale, _ = await finished_rave(c, ["mock:a?steps=1&delay=0"])
    age(svc, stale, 30)
    res = (await c.post("/api/rave/prune", json={"older_than_days": 7, "dry_run": False})).json()
    assert [i["id"] for i in res["items"]] == [stale]                   # the recent one is not old enough
    assert Path(agent((await c.get(f"/api/rave/{recent}")).json(), "a")["workspace"]).is_dir()
    again = (await c.post("/api/rave/prune", json={"older_than_days": 7, "dry_run": False})).json()
    assert again["items"] == [] and again["freed_bytes"] == 0
    rec = (await c.get(f"/api/rave/{stale}")).json()
    assert rec["pruned_at"]
    diff = (await c.get(f"/api/rave/{stale}/agents/a/diff")).json()
    assert "prune" in diff["note"]
    apply = await c.post(f"/api/rave/{stale}/agents/a/apply", json={})
    assert apply.status_code == 409 and code_of(apply) == "PRUNED"
    resume = await c.post(f"/api/rave/{stale}/resume", json={"agent": "a"})
    assert resume.status_code == 409 and code_of(resume) == "PRUNED"


async def test_a_backend_restart_does_not_make_an_old_rave_look_fresh(app):
    """`recover()` re-saves every record at start (new boot id): that must not reset the age prune looks at."""
    from bcc.rave import engine
    c, svc = app
    rid, _ = await finished_rave(c, ["mock:a?steps=1&delay=0"])
    age(svc, rid, 10)
    path = svc.rave.root / rid / "rave.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["boot_id"] = "an-earlier-boot"
    path.write_text(json.dumps(rec), encoding="utf-8")
    await svc.rave.recover()
    assert json.loads(path.read_text(encoding="utf-8"))["boot_id"] == engine.BOOT_ID       # it was re-saved
    assert json.loads(path.read_text(encoding="utf-8"))["updated_at"] > time.time() - 60   # ... and looks fresh
    (item,) = (await c.post("/api/rave/prune", json={"older_than_days": 7})).json()["items"]
    assert item["id"] == rid


async def test_a_recent_apply_counts_as_activity(app):
    c, svc = app
    rid, _ = await finished_rave(c, ["mock:a?steps=1&delay=0"])
    first = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    await c.post(f"/api/approvals/{first.json()['approval_id']}", json={"approve": True, "by": "owner:test"})
    assert (await c.post(f"/api/rave/{rid}/agents/a/apply", json={})).status_code == 200
    path = svc.rave.root / rid / "rave.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    old = time.time() - 10 * DAY
    rec["created_at"] = old
    for a in rec["agents"]:
        a["started_at"] = a["finished_at"] = old                    # everything old except the apply (just now)
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert (await c.post("/api/rave/prune", json={"older_than_days": 7})).json()["items"] == []


async def test_prune_report_names_results_that_were_never_applied(app):
    c, svc = app
    rid, v = await finished_rave(c, ["mock:a?steps=1&delay=0", "mock:b?steps=1&delay=0"])
    first = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    await c.post(f"/api/approvals/{first.json()['approval_id']}", json={"approve": True, "by": "owner:test"})
    done = await c.post(f"/api/rave/{rid}/agents/a/apply", json={"approval_id": first.json()["approval_id"]})
    assert done.status_code == 200
    age(svc, rid, 10)
    (item,) = (await c.post("/api/rave/prune", json={"older_than_days": 7})).json()["items"]
    assert item["unapplied"] == ["b"]                                   # a was applied, b would be lost


async def test_prune_skips_raves_with_continuable_agents_unless_asked(app, monkeypatch):
    c, svc = app
    use_stub(monkeypatch, login="none")
    approve_optin(svc, "claude")
    rid, v = await finished_rave(c, ["claude:cc", "mock:m?steps=1&delay=0"])
    assert agent(v, "cc")["status"] == "blocked"
    age(svc, rid, 10)
    res = (await c.post("/api/rave/prune", json={"older_than_days": 7})).json()
    assert res["items"] == [] and res["skipped"][0]["id"] == rid and "blocked" in res["skipped"][0]["reason"]
    res = (await c.post("/api/rave/prune", json={"older_than_days": 7, "include_blocked": True})).json()
    assert [i["id"] for i in res["items"]] == [rid]


async def test_prune_refuses_nonsense_days(app):
    c, _ = app
    assert (await c.post("/api/rave/prune", json={"older_than_days": -1})).status_code == 422
    assert (await c.post("/api/rave/prune", json={})).status_code == 422


def test_cli_prune_is_a_dry_run_unless_yes(capsys):
    class Client:
        def __init__(self):
            self.bodies = []

        def post(self, path, body=None, **kw):
            self.bodies.append((path, body))
            return {"dry_run": body["dry_run"], "older_than_days": 7, "freed_bytes": 3_145_728, "skipped": [],
                    "items": [{"id": "rv-12345678", "status": "done", "bytes": 3_145_728, "prompt": "p",
                               "unapplied": ["a"]}]}

    client = Client()
    args = rave_cli.build_parser().parse_args(["prune", "--older-than-days", "7"])
    assert rave_cli.run(client, args, rave_cli.Printer(False)) == 0
    out = capsys.readouterr().out
    assert client.bodies == [("/api/rave/prune", {"older_than_days": 7.0, "dry_run": True, "include_blocked": False})]
    assert "БУДЕТ удалено" in out and "3.0 МБ" in out and "РЕЗУЛЬТАТЫ НЕ ПРИМЕНЕНЫ: a" in out
    args = rave_cli.build_parser().parse_args(["prune", "--older-than-days", "7", "--yes", "--include-blocked"])
    assert rave_cli.run(client, args, rave_cli.Printer(False)) == 0
    assert client.bodies[-1][1] == {"older_than_days": 7.0, "dry_run": False, "include_blocked": True}
    bad = rave_cli.build_parser().parse_args(["prune"])
    assert rave_cli.run(client, bad, rave_cli.Printer(False)) == rave_cli.EXIT_USAGE


# ------------------------------------------------------------------ apply with an approval given elsewhere


async def approval_status(c, aid):
    rows = (await c.get("/api/approvals", params={"status": "all"})).json()
    return next(r["status"] for r in rows if r["id"] == aid)


async def test_apply_uses_the_approval_the_owner_already_gave_elsewhere_once(app):
    """Approve in the Approvals page / Telegram / `bossman approve`, press Применить again: no second question."""
    c, _ = app
    rid, v = await finished_rave(c, ["mock:a?steps=1&delay=0&file=README.md"])
    first = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert first.status_code == 202
    aid = first.json()["approval_id"]
    await c.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner:test"})
    done = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert done.status_code == 200 and done.json()["state"] == "APPLIED" and done.json()["approval_id"] == aid
    assert "by a" in (Path(v["repo"]) / "README.md").read_text()
    assert await approval_status(c, aid) == "consumed"                  # used exactly once
    again = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert again.status_code == 409 and code_of(again) == "ALREADY_APPLIED"


async def test_a_rejected_or_foreign_approval_is_never_used_for_apply(app):
    c, _ = app
    rid, v = await finished_rave(c, ["mock:a?steps=1&delay=0", "mock:b?steps=1&delay=0"])
    base = Path(v["repo"])
    first = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    aid = first.json()["approval_id"]
    await c.post(f"/api/approvals/{aid}", json={"approve": False, "by": "owner:test"})
    retry = await c.post(f"/api/rave/{rid}/agents/a/apply", json={})
    assert retry.status_code == 202 and retry.json()["approval_id"] != aid     # a rejection is not a permission
    assert not (base / "rave" / "a.md").exists()
    # an approval for agent a's preview does not authorise agent b
    await c.post(f"/api/approvals/{retry.json()['approval_id']}", json={"approve": True, "by": "owner:test"})
    other = await c.post(f"/api/rave/{rid}/agents/b/apply", json={})
    assert other.status_code == 202 and other.json()["approval_id"] != retry.json()["approval_id"]
    assert not (base / "rave" / "b.md").exists()
    assert await approval_status(c, retry.json()["approval_id"]) == "approved"   # a's approval is still unspent


# ------------------------------------------------------------------ a test command that can not even start


async def test_an_agent_is_still_finished_when_the_test_command_cannot_be_started(app, monkeypatch):
    """bossman-core missing (or the spawn refused) used to raise out of the finalisation and leave the agent
    `running` forever; now the agent ends and the tests are reported as NOT run."""
    c, _ = app

    async def refuse(*a, **kw):
        raise ModuleNotFoundError("No module named 'bossman'")

    monkeypatch.setattr(AgentCtx, "spawn", staticmethod(refuse))
    rid = await start(c, ["mock:a?steps=1&delay=0"], test="python -c pass")
    v = await wait_for(c, rid, settled)
    a = agent(v, "a")
    assert a["status"] == "done" and a["result_commit"]
    assert a["tests"]["ran"] is False and a["tests"]["passed"] is False
    assert "тесты не запущены" in a["tests"]["output_tail"] and "bossman" in a["tests"]["output_tail"]
