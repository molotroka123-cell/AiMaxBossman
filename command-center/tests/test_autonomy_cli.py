"""`bossman autonomy status|goals|journal verify|constitution status|constitution pin`."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bcc.autonomy.service import AutonomyService
from bcc.terminal_cli.cli import main

CC = Path(__file__).resolve().parents[1]


@pytest.fixture
def paths(tmp_path, monkeypatch):
    doc = tmp_path / "repo" / "CONSTITUTION.md"
    doc.parent.mkdir()
    doc.write_text("# rules\n", encoding="utf-8")
    pin = tmp_path / "local" / "constitution.sha256"
    data = tmp_path / "data"
    return {"doc": doc, "pin": pin, "data": data, "sha": hashlib.sha256(doc.read_bytes()).hexdigest(),
            "args": ["--data-dir", str(data), "--constitution", str(doc), "--pin-path", str(pin)]}


def run(capsys, *argv):
    code = main(["autonomy", *argv])
    return code, capsys.readouterr()


def test_routing_both_entry_points():
    from bossman.cli import is_terminal_call
    from bcc.terminal_cli.cli import TERMINAL_COMMANDS
    assert "autonomy" in TERMINAL_COMMANDS and is_terminal_call(["autonomy", "status"])


def test_help(capsys):
    code, out = run(capsys, "--help")
    assert code == 0 and "constitution" in out.out
    code, _ = run(capsys)
    assert code == 2


def test_status_blocked_without_pin_then_ready(capsys, paths):
    code, out = run(capsys, "status", "--json", *paths["args"])
    s = json.loads(out.out)
    assert code == 5 and s["loop"] == "BLOCKED" and "not pinned" in s["reason"] and s["goals_total"] == 0
    paths["pin"].parent.mkdir(parents=True)
    paths["pin"].write_text(paths["sha"] + "\n")
    code, out = run(capsys, *paths["args"], "status")
    assert code == 0 and "loop: READY" in out.out and "lease: free" in out.out


def test_constitution_status(capsys, paths):
    code, out = run(capsys, "constitution", "status", *paths["args"])
    assert code == 5 and out.out.startswith("BLOCKED")
    paths["pin"].parent.mkdir(parents=True)
    paths["pin"].write_text(paths["sha"])
    code, out = run(capsys, "constitution", "status", "--json", *paths["args"])
    assert code == 0 and json.loads(out.out)["status"] == "OK"


def test_pin_is_refused_from_an_agent_or_non_tty(capsys, paths, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    code, out = run(capsys, "constitution", "pin", *paths["args"])
    assert code == 5 and "REFUSED" in out.out and not paths["pin"].exists()
    monkeypatch.delenv("CLAUDECODE")
    for k in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CI",
              "GITHUB_ACTIONS", "BOSSMAN_AGENT_SESSION"):
        monkeypatch.delenv(k, raising=False)
    code, out = run(capsys, "constitution", "pin", *paths["args"])        # pytest stdin is not a TTY
    assert code == 5 and "interactive" in out.out and not paths["pin"].exists()
    journal = AutonomyService(paths["data"]).journal
    assert [e["kind"] for e in journal.entries()] == ["constitution.pin_refused"] * 2


def test_goals_and_journal_verify(capsys, paths):
    from .test_autonomy_api import goal
    auto = AutonomyService(paths["data"])
    auto.goals.create(goal())
    auto.goals.block("JEFF-0042", "ambiguous state")
    code, out = run(capsys, "goals", *paths["args"])
    assert code == 0 and "JEFF-0042" in out.out and "BLOCKED: ambiguous state" in out.out
    code, out = run(capsys, "goals", "--state", "PLANNED", "--json", *paths["args"])
    assert json.loads(out.out) == {"items": []}
    code, out = run(capsys, "journal", "verify", *paths["args"])
    assert code == 0 and out.out.startswith("OK: 2 entries")
    with open(auto.journal.path, "ab") as fh:
        fh.write(b'{"seq": 3}\n')
    code, out = run(capsys, "journal", "verify", "--json", *paths["args"])
    assert code == 1 and json.loads(out.out)["ok"] is False


def test_real_entry_point_routes_to_autonomy(paths):
    code = "import sys; from bossman.cli import main; sys.exit(main(sys.argv[1:]))"
    env = {**os.environ, "PYTHONPATH": f"{CC}{os.pathsep}{CC.parent / 'bossman-core'}"}
    p = subprocess.run([sys.executable, "-c", code, "autonomy", "journal", "verify", "--json", *paths["args"]],
                       cwd=CC, capture_output=True, text=True, timeout=120, env=env)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["ok"] is True
