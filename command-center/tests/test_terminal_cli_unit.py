"""Bossman 1.2 terminal — pure parts: injection neutralisation, slash parsing,
event normalisation, status cells and rendering snapshots (160 and 90 columns),
history secret filter, the `bossman` dispatch that keeps the Core commands."""
from __future__ import annotations

import io

import pytest

pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

from bcc.terminal_cli import slash  # noqa: E402
from bcc.terminal_cli.console import make_console, sanitize  # noqa: E402
from bcc.terminal_cli.human import View, money, render_status_bar, status_cells  # noqa: E402
from bcc.terminal_cli.records import normalize, state_of  # noqa: E402

# ----------------------------------------------------------------- injection


@pytest.mark.parametrize("hostile", [
    "ok\x1b[2J\x1b[Hwiped",                      # clear screen + home
    "copy\x1b]52;c;ZXZpbA==\x07me",              # OSC 52 clipboard write
    "title\x1b]0;owned\x1b\\x",                  # window title (ST-terminated)
    "safe\rC:\\Bossman> fake prompt",            # CR overwrite
    "x\x9b31mred\x9d0;t\x07y",                   # 8-bit CSI / OSC
    "bi‮di⁦rtl",                       # bidi override / isolate
    "bell\x07back\x08space\x00nul",
])
def test_untrusted_text_cannot_drive_the_terminal(hostile):
    clean = sanitize(hostile)
    assert not any(ch in clean for ch in "\x1b\x07\x08\x00\r\x9b\x9d‮⁦")
    assert sanitize(clean) == clean                   # idempotent


def test_sanitize_keeps_cyrillic_and_newlines():
    assert sanitize("Привет,\nмир — ✓") == "Привет,\nмир — ✓"


def test_rich_markup_in_model_text_is_printed_literally():
    buf = io.StringIO()
    console = make_console(stream=buf, plain=True, width=100)
    view = View(console, plain=True)
    view.on_record({"type": "assistant_message", "text": "[bold red]не разметка[/] [link=http://x]y[/link]"})
    out = buf.getvalue()
    assert "[bold red]не разметка[/]" in out and "[link=http://x]" in out
    assert "\x1b" not in out


# ----------------------------------------------------------------- slash


def test_slash_parsing():
    assert slash.parse("привет").kind == "message"
    assert slash.parse("   ").kind == "empty"
    assert slash.parse("//etc/hosts").text == "/etc/hosts"
    p = slash.parse("/code --allow src\\calc.py --verify \"tests/test a.py\" почини сложение")
    assert p.kind == "command" and p.name == "code"
    assert p.args[:4] == ["--allow", "src\\calc.py", "--verify", "tests/test a.py"]
    assert slash.parse("/quit").name == "exit"
    assert slash.parse("/model use qwen").name == "models"
    unknown = slash.parse("/rm -rf")
    assert unknown.kind == "unknown" and "/help" in unknown.error


def test_every_directive_command_exists():
    for name in ("help", "status", "tasks", "models", "skills", "tools", "memory", "diff", "approve",
                 "deny", "pause", "stop", "resume", "evolve", "exit", "keys", "panel"):
        assert name in slash.COMMANDS, name


# ----------------------------------------------------------------- records


def test_reasoning_only_when_the_provider_sent_it():
    assert normalize({"kind": "run.reasoning_delta", "text": "думаю", "step": 1})[0]["type"] == "thinking"
    assert normalize({"kind": "task.progress", "step": 1, "max_steps": 4})[0]["type"] == "step"
    assert all(r["type"] != "thinking" for r in normalize({"kind": "run.assistant_message", "text": "ок"}))


def test_states_and_unknown_cost():
    assert [state_of(s) for s in ("completed", "failed", "stopped", "blocked", "waiting_approval",
                                  "paused", "running")] == [
        "PASS", "FAIL", "STOPPED", "BLOCKED", "WAIT_APPROVAL", "PARTIAL", "RUNNING"]
    usage = normalize({"kind": "run.usage", "pricing_known": False, "cost_usd": 0.0, "tokens_in": 5})[0]
    assert usage["cost_usd"] is None if "cost_usd" in usage else True
    assert money(None) == "—" and money(0.12) == "$0.12"


def test_memory_recall_count_is_read_from_the_log_line():
    rec = normalize({"kind": "run.log", "log_kind": "memory.recalled", "message": "память: 3 записей"})[0]
    assert rec["type"] == "memory" and rec["count"] == 3


# ----------------------------------------------------------------- rendering


def _render(cells, width, plain=False):
    buf = io.StringIO()
    console = make_console(stream=buf, plain=True, width=width)
    for line in render_status_bar(cells, width, plain=plain):
        console.print(line)
    return buf.getvalue()


def test_status_bar_hides_unknown_memory_and_labels_mock():
    cells = status_cells(mode="chat", model="mock-chat", model_locality="local", model_kind="MOCK_MODEL",
                         approvals="on-demand", context=None, computer="enabled")
    labels = [c.label for c in cells]
    assert "Memory" not in labels, "unknown context usage is hidden, never invented"
    assert any("MOCK_MODEL" in c.value for c in cells)
    with_ctx = status_cells(mode="chat", model="m", model_locality=None, model_kind=None,
                            approvals="on-demand", context=(862, 2048), computer=None)
    assert any(c.label == "Memory" and c.value == "42% (862/2048)" for c in with_ctx)


def test_status_bar_snapshot_160_and_90_columns():
    cells = status_cells(mode="chat", model="qwen3-coder", model_locality="local", model_kind="REAL_MODEL",
                         approvals="on-demand", context=(862, 2048), computer="enabled")
    wide = _render(cells, 160).splitlines()
    assert len(wide) == 3, wide                      # one boxed row
    assert wide[1].startswith("│ Mode: chat │ Model: qwen3-coder (local) │ Approvals: on-demand │ "
                              "Memory: 42% (862/2048) │ Computer Control: enabled")
    assert all(len(line) <= 160 for line in wide)
    narrow = _render(cells, 90).splitlines()
    assert len(narrow) > 3, "cells wrap onto more rows instead of being cut"
    assert all(len(line) <= 90 for line in narrow)
    assert "Computer Control: enabled" in "\n".join(narrow)
    plain = _render(cells, 90, plain=True)
    assert "│" not in plain and "Mode: chat | Model:" in plain


def test_conversation_snapshot_single_column():
    buf = io.StringIO()
    view = View(make_console(stream=buf, plain=True, width=100), plain=True)
    view.user("Улучши себя")
    view.on_record({"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "calc.py"}})
    view.on_record({"type": "tool_result", "tool_use_id": "c1", "name": "read_file", "is_error": False,
                    "summary": "2 строки", "duration_ms": 35})
    view.on_record({"type": "assistant_message",
                    "text": "План:\n1. **Проверить** – тесты\nNext: запустить тесты"})
    lines = buf.getvalue().splitlines()
    text = "\n".join(lines)
    assert "You ›      Улучши себя" in text
    assert "Bossman ›  ● read_file(path=calc.py)" in text
    assert "⎿ ✓ 2 строки  (35 мс)" in text
    assert "1. Проверить – тесты" in text
    assert "Next: запустить тесты" in text
    assert all(len(line) <= 100 for line in lines)


# ----------------------------------------------------------------- history


def test_history_filter_drops_secrets():
    from bcc.terminal_cli.chat import filter_history_line
    line = filter_history_line("мой ключ " + "sk-" + "ant-abcdefghijklmnop1234567890ABCDEF" + " и token=abcd12345")
    assert "sk-ant-" not in line and "abcd12345" not in line


# ----------------------------------------------------------------- dispatch


def test_bossman_entry_point_keeps_core_commands():
    cli = pytest.importorskip("bossman.cli")
    for argv in (["serve"], ["task", "x", "--agent", "a"], ["project", "state", "s"],
                 ["models", "list", "--all"], ["--help"]):
        assert not cli.is_terminal_call(argv), argv
    for argv in ([], ["chat"], ["exec", "--input-file", "f"], ["-p", "hi"], ["status", "--json"],
                 ["keys", "set", "anthropic"], ["market", "status"]):
        assert cli.is_terminal_call(argv), argv


def test_market_terminal_uses_the_read_only_collector(tmp_path, capsys):
    from bcc.terminal_cli.cli import main

    root = str(tmp_path / "market")
    assert main(["market", "status", "--root", root]) == 0
    assert '"attempted": 0' in capsys.readouterr().out
    assert main(["market", "stop", "--root", root]) == 0
    assert (tmp_path / "market" / "STOP").exists()
    assert main(["market", "watch", "--cadence", "0", "--root", root]) != 0


# ----------------------------------------------------------------- launchers


def test_windows_launcher_templates_use_the_archive_runtime():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "tools" / "terminal_launchers.py"
    spec = importlib.util.spec_from_file_location("terminal_launchers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.TERMINAL_LAUNCHERS) == {"Bossman-CLI.cmd", "Bossman-Terminal.cmd", "bossman.cmd"}
    for name, body in mod.TERMINAL_LAUNCHERS.items():
        assert '"%BOSSMAN_HOME%runtime\\python.exe" -m bossman.cli' in body, name
        assert 'call "%BOSSMAN_HOME%app-support\\_env.cmd"' in body, name
        assert "chcp 65001 >nul" in body and "%*" in body, name
        for forbidden in ("git ", "pip install", "..\\", "\\command-center\\"):
            assert forbidden not in body, (name, forbidden)
    # machine stdout stays JSON: the env script's messages go to stderr
    assert '_env.cmd" 1>&2' in mod.TERMINAL_LAUNCHERS["bossman.cmd"]
    assert "chat %*" in mod.TERMINAL_LAUNCHERS["Bossman-CLI.cmd"]
