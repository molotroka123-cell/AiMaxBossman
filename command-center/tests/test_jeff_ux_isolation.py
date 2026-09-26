"""Jeff UX isolation contracts."""
from __future__ import annotations

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
    )
    assert not [item for item in forbidden if item in js]
    assert "api.system()" in js
    assert "api.identity()" in js


def test_computer_use_is_visibly_locked_for_jeff():
    html = _read(UI / "jeff.html")
    assert "Computer Use" in html
    assert "Недоступно Jeff" in html
    assert "capability locked" in html
    assert "disabled" in html


def test_voice_and_avatar_are_local_preferences_only():
    js = _read(UI / "jeff.js")
    assert "jeff.ux.prefs.v1" in js
    assert "speechSynthesis" in js
    assert "SpeechRecognition" in js
    assert "api.update" not in js
    assert "api.create" not in js


def test_jeff_desktop_uses_same_backend_but_separate_window_profile():
    launcher = _read(ROOT / "command-center" / "bcc" / "jeff_desktop.py")
    assert 'base_url + "jeff.html"' in launcher
    assert '"jeff-desktop-profile"' in launcher
    assert "_same_build(local, running)" in launcher
    assert "_BackgroundServer" in launcher


def test_shortcut_is_separate_and_does_not_replace_bossman_shortcut():
    script = _read(ROOT / "tools" / "desktop" / "install-jeff-shortcut.ps1")
    assert '"Jeff.lnk"' in script
    assert "-m bcc.jeff_desktop" in script
    assert "Bossman.lnk" not in script
