"""cli.delegate: argv building, runner with a fake CLI, STOP, cost parsing, bypass gate, ledger."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from bcc import permissions
from bcc.features import tools_cli as tc

WIN = sys.platform == "win32"
ENV = {"PATH": "C:\\Windows\\System32" if WIN else "/usr/bin:/bin", "SYSTEMROOT": "C:\\Windows"}


def _wrapper(tmp_path: Path, name: str, win_body: str, sh_body: str) -> Path:
    p = tmp_path / (name + (".cmd" if WIN else ""))
    p.write_text(win_body if WIN else "#!/bin/sh\n" + sh_body, encoding="utf-8")
    if not WIN:
        p.chmod(0o755)
    return p


def test_argv_modes_claude_and_codex():
    a = tc._argv("claude", "claude", "do x", "auto", None)
    assert a[:3] == ["claude", "-p", "do x"] and "acceptEdits" in a and "bypassPermissions" not in a
    b = tc._argv("claude", "claude", "do x", "bypass", "haiku")
    assert "bypassPermissions" in b and b[-2:] == ["--model", "haiku"]
    c = tc._argv("codex", "codex", "do y", "auto", None)
    assert c[:3] == ["codex", "exec", "--json"] and "--sandbox" in c and c[-1] == "do y"
    assert "--dangerously-bypass-approvals-and-sandbox" in tc._argv("codex", "codex", "do y", "bypass", None)


def test_runner_reports_answer_and_cost_from_claude_json(tmp_path):
    w = _wrapper(tmp_path, "claude", '@echo {"result": "ok from fake", "total_cost_usd": 0.01}\n',
                 'echo \'{"result": "ok from fake", "total_cost_usd": 0.01}\'\n')
    res = tc.run_cli("claude", "prompt", tmp_path, exe=str(w), env=ENV, minutes=1)
    assert res["rc"] == 0 and res["answer"].startswith("ok from fake") and res["cost_usd"] == 0.01 and res["stopped"] is False


def test_stop_kills_a_long_run(tmp_path):
    w = _wrapper(tmp_path, "sleepy", "@ping -n 30 127.0.0.1 > nul\n", "sleep 30\n")
    res = tc.run_cli("codex", "p", tmp_path, exe=str(w), env=ENV, minutes=1, stop=lambda: True)
    assert res["stopped"] is True and "STOP" in res["answer"] and res["seconds"] < 25


def test_missing_binary_is_a_clean_failure(tmp_path):
    res = tc.run_cli("codex", "p", tmp_path, exe=str(tmp_path / "no-such-cli-xyz.exe"), env=ENV, minutes=1)
    assert res["rc"] == 127 and res["stopped"] is False and "no-such-cli-xyz" in res["answer"]


def test_codex_jsonl_answer_is_last_agent_message():
    lines = [json.dumps({"msg": {"type": "agent_message", "message": "first"}}),
             json.dumps({"msg": {"type": "agent_message", "message": "final answer"}}), "not json"]
    assert tc._parse_codex("\n".join(lines))[0] == "final answer"


def test_bypass_is_a_dangerous_permission_and_the_hook_floors_to_ask():
    assert "cli.bypass" in permissions.ALL_PERMISSIONS and permissions.is_dangerous("cli.bypass")
    assert tc._effect_hook({"mode": "bypass"})[0] == "ask" and tc._effect_hook({"mode": "auto"}) is None
    assert permissions.needs_approval({"permissions": {}}, "cli.bypass") is True
    assert permissions.needs_approval({"permissions": {"cli.bypass": True}}, "cli.bypass") is False


def test_daily_ledger_sums_costs_and_ignores_garbage(tmp_path):
    p = tmp_path / f"ledger-{time.strftime('%Y-%m-%d')}.jsonl"
    p.write_text('{"cost_usd": 1.5}\n{"cost_usd": "bad"}\n{"cost_usd": 0.25}\n', encoding="utf-8")
    assert tc._spent_today(tmp_path) == pytest.approx(1.75)


def test_live_checkout_is_the_running_command_center():
    assert (tc._live_checkout() / "bcc").is_dir()
