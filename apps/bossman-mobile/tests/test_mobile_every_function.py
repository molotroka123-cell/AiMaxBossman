"""Live audit 25.10 «Проверяй каждую функцию»: regressions for the FAILs found button by button on the owner PC.

1. Web terminal ran in roots[0] (a Python Lib folder) -> git status «not a git repository», pytest preset «file not found».
2. A command needing approval (python -V, echo test) came back as HTTP 202 wrapped in a 200 body: no output, no prompt.
3. `claude` (interactive TUI) in a non-TTY web terminal: refused with a clear message (three-model decision, option A).
4. Mode chips only PATCHed the session; the text still went to /threads/send, so CODE / PC / /improve changed nothing.
5. The «$» badge was a static «$0.00».
"""
from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest

import bossmobile.api as api_mod
from bossmobile.api import terminal_refusal

UI = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"


def _calls(env, path, method="POST"):
    return [c for c in env.fake.calls if c["path"] == path and c["method"] == method]


# ---------------------------------------------------------------- 3. interactive commands
@pytest.mark.parametrize("cmd", [
    "claude", "claude.exe", "C:\\tools\\claude.cmd", 'claude -p "привет"', "npx @anthropic-ai/claude-code",
    "cd apps && claude", "FOO=1 claude", "vim README.md", "nano x", "python", "node", "pwsh", "ssh host",
    "ollama run gemma",
])
def test_terminal_refusal_blocks_interactive(cmd):
    assert terminal_refusal(cmd)


@pytest.mark.parametrize("cmd", [
    "python -V", "echo test", "git status --short --branch", "git diff --stat",
    "python -m pytest command-center/tests/test_terminal_quick_presets.py -q", 'ollama run gemma "привет"',
    "claudette --help", "echo claude", "",
])
def test_terminal_refusal_allows_finite_commands(cmd):
    assert terminal_refusal(cmd) is None


def test_terminal_run_refuses_claude_without_reaching_bossman(authed):
    r = authed.post("/m/api/terminal/run", json={"command": "claude", "mode": "project_host"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "TERMINAL_INTERACTIVE" and "Чат" in d["message"]
    assert not _calls(authed, "/api/terminal/run")          # nothing started, no approval record created


def test_terminal_run_forwards_read_only_command(authed):
    r = authed.post("/m/api/terminal/run", json={"command": "git status", "mode": "project_host"})
    assert r.status_code == 200 and r.json()["session_id"] == "term-01"
    assert len(_calls(authed, "/api/terminal/run")) == 1


# ---------------------------------------------------------------- 2. approval needed -> real 202 with the id
def test_terminal_run_needs_approval_is_a_202_with_id(authed):
    r = authed.post("/m/api/terminal/run", json={"command": "echo test", "mode": "project_host"})
    assert r.status_code == 202
    assert r.json() == {"approval_required": True, "approval_id": 77, "decision": "ask",
                        "message": "нужно подтверждение владельца: команда не из списка только-чтения"}
    # the same command presented with the approved id starts a session (negative control for the 202 branch)
    r2 = authed.post("/m/api/terminal/run", json={"command": "echo test", "mode": "project_host", "approval_id": 77})
    assert r2.status_code == 200 and r2.json()["session_id"] == "term-01"


def test_terminal_run_requires_login(env):
    assert env.c.post("/m/api/terminal/run", json={"command": "git status"}).status_code == 401


# ---------------------------------------------------------------- 1. default cwd = this checkout
def test_terminal_roots_default_is_this_checkout(authed, monkeypatch):
    monkeypatch.setattr(api_mod, "REPO_ROOT", Path("C:/Users/asd/Bossman/wt-final"))
    d = authed.c.get("/m/api/terminal/roots").json()
    assert d["default"] == "C:/Users/asd/Bossman/wt-final"
    assert d["roots"] == ["C:/Users/asd/Bossman", "C:/Users/asd/Bossman/wt-final"]


def test_terminal_roots_default_falls_back_to_first_root(authed, monkeypatch):
    monkeypatch.setattr(api_mod, "REPO_ROOT", Path("D:/elsewhere/repo"))
    assert authed.c.get("/m/api/terminal/roots").json()["default"] == "C:/Users/asd/Bossman"


def test_repo_root_points_at_the_checkout():
    assert (api_mod.REPO_ROOT / "apps" / "bossman-mobile" / "bossmobile" / "api.py").is_file()


# ---------------------------------------------------------------- 4. mode chips reach the dispatcher, local-only
def test_mode_message_forwarded_strict_and_local(authed):
    r = authed.post("/m/api/agent/sessions/S1/messages", json={"text": "проверь", "mode": "COMPUTER", "agent_id": 1,
                                                                "client_request_id": "m-abc-12345678"})
    assert r.status_code == 200 and r.json()["task"]["id"] == 7
    sent = json.loads(_calls(authed, "/api/agent/sessions/S1/messages")[-1]["body"])
    assert sent == {"text": "проверь", "mode": "COMPUTER", "agent_id": 1, "allow_cloud": False,
                    "client_request_id": "m-abc-12345678"}


def test_mode_message_rejects_cloud_agent_and_extra_fields(authed):
    r = authed.post("/m/api/agent/sessions/S1/messages", json={"text": "x", "mode": "AGENT", "agent_id": 3})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "BM_LOCAL_ONLY"
    for bad in ({"text": "x", "mode": "AGENT"},                                        # no agent: Auto could go cloud
                {"text": "x", "mode": "AGENT", "agent_id": 1, "worker": "glm"},          # no worker from the phone
                {"text": "x", "mode": "AGENT", "agent_id": 1, "allow_cloud": True},
                {"text": "x", "mode": "ROOT", "agent_id": 1}):
        assert authed.post("/m/api/agent/sessions/S1/messages", json=bad).status_code == 422, bad
    assert not _calls(authed, "/api/agent/sessions/S1/messages")


# ---------------------------------------------------------------- 5. cost badge numbers only
def test_cost_endpoint_returns_numbers_only(authed):
    d = authed.c.get("/m/api/agent/sessions/S1/cost").json()
    assert d == {"spent_usd": 0.0123, "budget_usd": 5.0, "budget_remaining_usd": 4.9877, "mode": "AGENT"}
    assert "secret turn" not in json.dumps(d, ensure_ascii=False)


def test_cost_endpoint_rejects_bad_id(authed):
    assert authed.c.get("/m/api/agent/sessions/..%2Fx/cost").status_code == 404


# ---------------------------------------------------------------- UI wiring
def test_ui_wiring_for_audit_fixes():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "/agent/sessions/${encodeURIComponent(S.threadId)}/messages`, { ...payload, mode }" in js
    assert "r.default ||" in js and "async function termCwd()" in js
    assert "res.approval_required" in js and "btn-term-approve" in js and "approval_id = approvalId" in js
    assert "err.code === 'TERMINAL_INTERACTIVE'" in js
    assert "async function refreshCost()" in js and "/cost`" in js
    # the old roots[0]-only cwd is gone
    assert "const cwd = (rootsResp.roots && rootsResp.roots[0]) || undefined;" not in js



def test_terminal_202_accepts_legacy_detail_shape(authed):
    r = authed.post("/m/api/terminal/run", json={"command": "echo legacy", "mode": "project_host"})
    assert r.status_code == 202 and r.json()["approval_id"] == 78
