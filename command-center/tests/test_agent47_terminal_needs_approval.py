"""Security audit 24.10: Kimi K3.1 Pro (agent 47) has PC access, but EVERY terminal.run needs the owner's approval.

The live agent row (24.10) is mirrored here: tools code.* + terminal.*, permissions cloud_pc_access/filesystem.read/
terminal.read = True and NO terminal.run. decide_effect must return ASK («право terminal.run не выдано агенту») for
terminal.run (even a read-only `git status`) and terminal.stdin, while read-only tools stay AUTO (negative control: the gate is not just «ask all»).
"""
from __future__ import annotations

from bcc.features.tools_terminal import SPECS
from bcc.tools import allowed_tools_for, decide_effect

AGENT47 = {
    "id": 47,
    "tools": ["code.search", "code.expand", "code.status", "code.workspace_map",
              "terminal.run", "terminal.status", "terminal.stdin", "terminal.kill"],
    "permissions": {"mobile_cloud_ok": True, "cloud_pc_access": True,
                    "filesystem.read": True, "terminal.read": True},
}


def _spec(name):
    return {s.name: s for s in SPECS}[name]      # the exact specs tools_terminal registers at startup


def test_agent47_sees_its_tools_but_terminal_run_asks():
    assert "terminal.run" in allowed_tools_for({}, AGENT47)
    effect, why = decide_effect(_spec("terminal.run"), {"command": "git status"}, AGENT47)
    assert effect == "ask", (effect, why)
    assert "terminal.run" in why and "не выдано" in why


def test_agent47_stdin_and_kill_also_ask():
    for name, args in (("terminal.stdin", {"session_id": "x", "text": "dir\n"}), ("terminal.kill", {"session_id": "x"})):
        effect, why = decide_effect(_spec(name), args, AGENT47)
        assert effect == "ask", (name, effect, why)


def test_negative_control_terminal_status_is_auto_and_a_granted_run_would_be_auto():
    effect, _ = decide_effect(_spec("terminal.status"), {"session_id": "x"}, AGENT47)
    assert effect == "auto"
    granted = {**AGENT47, "permissions": {**AGENT47["permissions"], "terminal.run": True}}
    effect, _ = decide_effect(_spec("terminal.run"), {"command": "git status"}, granted)
    assert effect == "auto", "control: with the right granted the same call is AUTO, so ASK above comes from the missing grant"
