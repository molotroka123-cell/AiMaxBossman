"""Jeff UX isolation contracts (static): the window is participant-only."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "command-center" / "ui"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_jeff_frontend_has_no_owner_mutation_surface():
    js = _read(UI / "jeff.js")
    forbidden = (
        "createTask(",
        "taskAction(",
        "/api/control-plane",
        "/api/terminal",
        "/api/browser",
        "/api/opencode",
        "computer.control",
        "shell.exec",
        "api.raw(",
        "./api.js",           # the owner Command Center session helper
        "access_token",
    )
    assert not [item for item in forbidden if item in js]
    endpoints = set(re.findall(r"['\"`](/api/[\w/.-]*)", js))
    assert endpoints, "jeff.js must talk to the Jeff web API"
    assert all(path.startswith("/api/jeff/") for path in endpoints), endpoints


def test_computer_use_is_visibly_locked_for_jeff():
    html = _read(UI / "jeff.html")
    assert "Computer Use" in html
    assert "Недоступно Jeff" in html
    assert "capability locked" in html
    assert "disabled" in html
    assert "Command Center" not in html.split("<footer>")[0].split("topbar")[1][:400]


def test_voice_is_local_first_with_text_fallback():
    js = _read(UI / "jeff.js")
    assert "jeff.ux.prefs.v1" in js
    assert "/api/jeff/voice/transcribe" in js and "/api/jeff/voice/speak" in js
    assert "NotAllowedError" in js and "NotFoundError" in js      # mic errors are explained
    assert "speechSynthesis" in js                                  # system voice fallback
    assert "ответ показан текстом" in js                            # no voice -> text


def test_replies_are_escaped_not_rendered_as_html():
    js = _read(UI / "jeff.js")
    assert "function formatReply" in js and "escapeHtml(text)" in js


def test_jeff_desktop_runs_its_own_server_and_profile_not_command_center():
    launcher = _read(ROOT / "command-center" / "bcc" / "jeff_desktop.py")
    assert '"jeff-desktop-profile"' in launcher
    assert "from .pit.web import create_app" in launcher
    assert "same_build(local, running)" in launcher
    for owner_bit in ("_BackgroundServer", "read_access_token", "desktop.lock", "_read_lock",
                      "from .app import"):
        assert owner_bit not in launcher.split('"""', 2)[2], owner_bit


def test_shortcut_is_separate_and_does_not_replace_bossman_shortcut():
    script = _read(ROOT / "tools" / "desktop" / "install-jeff-shortcut.ps1")
    assert '[string]$Name = "Jeff"' in script
    assert "-m bcc.jeff_desktop" in script
    assert "Bossman.lnk" not in script and "BOSSMAN.lnk" not in script
    assert "$Link.WorkingDirectory = $CommandCenter" in script


def test_login_handler_is_bound_before_session_check():
    js = _read(UI / "jeff.js")
    assert js.rfind("bindUi();") < js.rfind("await call('/api/jeff/me')")


def test_closing_jeff_only_stops_the_server_it_started():
    launcher = _read(ROOT / "command-center" / "bcc" / "jeff_desktop.py")
    assert "if started is not None:\n            started.stop()" in launcher
