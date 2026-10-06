"""Security audit 2026-10-05: the owner's process ("thinking") pane must not outlive the session."""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"


def test_login_screen_resets_the_thinking_pane():
    app = (UI / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function showLogin("):]
    body = body[:body.index("\n}\n")]
    assert "thinking.reset()" in body


def test_reset_clears_runs_events_and_hides_the_pane():
    js = (UI / "thinking.js").read_text(encoding="utf-8")
    m = re.search(r"function reset\(\) \{(.*?)\n  \}", js, re.S)
    assert m, "mountThinking must define reset()"
    for part in ("runs.clear()", "events.length = 0", "seeded = false", "setOpen(false)"):
        assert part in m.group(1)
    assert re.search(r"return \{[^}]*\breset\b", js), "reset is exported"
