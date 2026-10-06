"""Agentic Rave — agent-spec parsing and the `bossman rave` / `/rave` CMD surface."""
from __future__ import annotations

import pytest

from bcc.rave import cli as rave_cli
from bcc.rave.spec import SpecError, parse_agent, parse_agents


def test_spec_forms():
    s = parse_agent("local:qwen@bossman-fast-qwen36-35b-a3b-q5:latest?max_steps=5")
    assert (s.connector, s.name, s.model, s.params) == ("local", "qwen", "bossman-fast-qwen36-35b-a3b-q5:latest",
                                                        {"max_steps": "5"})
    assert parse_agent("mock:a").model is None
    specs = parse_agents(["mock:a,mock:b?file=x.txt&steps=2", "claude:c"])
    assert [x.name for x in specs] == ["a", "b", "c"] and specs[1].params == {"file": "x.txt", "steps": "2"}


@pytest.mark.parametrize("bad", ["a", "gpt:a", "mock:", "mock:a b", "mock:../x", "mock:a@bad model",
                                 "mock:a?BAD=1"])
def test_spec_refusals(bad):
    with pytest.raises(SpecError):
        parse_agent(bad)


def test_spec_list_limits():
    with pytest.raises(SpecError):
        parse_agents(["mock:a,mock:a"])
    with pytest.raises(SpecError):
        parse_agents([",".join(f"mock:a{i}" for i in range(9))])
    with pytest.raises(SpecError):
        parse_agents([""])


def test_rave_is_a_terminal_command_in_both_entry_points():
    from bossman.cli import is_terminal_call
    from bcc.terminal_cli.cli import TERMINAL_COMMANDS
    assert is_terminal_call(["rave", "status", "rv-12345678"])
    assert "rave" in TERMINAL_COMMANDS


def test_rave_help_goes_through_the_terminal_cli(capsys):
    from bcc.terminal_cli.cli import main
    assert main(["rave", "--help"]) == 0
    assert "Agentic Rave" in capsys.readouterr().out


def test_slash_rave_is_a_chat_command():
    from bcc.terminal_cli import slash
    p = slash.parse('/rave "fix the bug" --agents mock:a,mock:b')
    assert p.kind == "command" and p.name == "rave" and p.args == ["fix the bug", "--agents", "mock:a,mock:b"]


RAVE = {"id": "rv-12345678", "status": "partial", "base_commit": "abcdef1234567", "scratch": True,
        "prompt": "p", "applied": {},
        "conflicts": [{"file": "README.md", "agents": ["a", "b"], "kind": "both_modified",
                       "auto_mergeable": False, "conflict_hunks": 1, "artifacts": "X:/c"}],
        "agents": [{"name": "a", "provider": "mock (scripted, not a model)", "model": "-", "auth": "none",
                    "status": "done", "step": 2, "steps_total": 2, "changed_files": [{"path": "README.md"}],
                    "tests": {"passed": True}, "answer": "ok"},
                   {"name": "cc", "provider": "Claude Code CLI", "model": "default",
                    "auth": "subscription (claude login)", "status": "blocked", "step": 0, "steps_total": None,
                    "changed_files": [], "error": "claude: не выполнен вход — `claude auth login --claudeai`"},
                   {"name": "r", "provider": "mock", "model": "-", "auth": "none", "status": "paused",
                    "pause_reason": "recovered_after_restart", "step": 3, "steps_total": 6, "changed_files": []}]}


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **kw):
        self.calls.append(("GET", path))
        return RAVE

    def post(self, path, body=None, **kw):
        self.calls.append(("POST", path, body))
        return {"changed": ["a"], "rave": RAVE}


def test_status_table_shows_provider_auth_status_tests_errors_and_conflicts(capsys):
    args = rave_cli.build_parser().parse_args(["status", "rv-12345678"])
    assert rave_cli.run(FakeClient(), args, rave_cli.Printer(False)) == 0
    out = capsys.readouterr().out
    assert "Claude Code CLI / default" in out and "subscription (claude login)" in out
    assert "PASS" in out and "claude auth login" in out
    assert "paused*" in out and "bossman rave resume rv-12345678" in out
    assert "README.md: a × b" in out and "все версии сохранены" in out


def test_chat_rave_uses_the_chat_connection():
    lines, client = [], FakeClient()
    rave_cli.chat_command(client, ["pause", "rv-12345678", "--agent", "a"], lines.append)
    assert ("POST", "/api/rave/rv-12345678/pause", {"agent": "a"}) in client.calls
    assert any("пауза: агент a" in ln for ln in lines)


def test_start_without_agents_is_a_usage_error(capsys):
    args = rave_cli.build_parser().parse_args(["do something"])
    assert rave_cli.run(FakeClient(), args, rave_cli.Printer(False)) == rave_cli.EXIT_USAGE
