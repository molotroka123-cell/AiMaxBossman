"""Verify Web Terminal quick-command presets in command-center/ui/pages/terminal.js."""
from __future__ import annotations

from pathlib import Path


def test_terminal_ui_defines_and_renders_quick_commands() -> None:
    terminal_js = (
        Path(__file__).resolve().parents[1] / "ui" / "pages" / "terminal.js"
    ).read_text(encoding="utf-8")
    assert "QUICK_COMMANDS" in terminal_js
    assert "'git status'" in terminal_js
    assert "'pytest -q'" in terminal_js
    assert "QUICK_COMMANDS.map" in terminal_js
