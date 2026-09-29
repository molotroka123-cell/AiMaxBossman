"""Заставка при первом открытии окна: файл на месте, попадает в установку и не мешает автоматизации."""
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"


def test_intro_video_ships_with_ui():
    video = UI / "intro" / "bossman-intro.webm"
    assert video.is_file()
    assert video.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"  # EBML/WebM
    assert video.stat().st_size < 3 * 1024 * 1024
    setup = (UI.parent / "setup.py").read_text(encoding="utf-8")
    assert '".webm"' in setup, "setup.py must copy the splash video into the installed _ui"
    assert "*.webm" in (UI.parent / "MANIFEST.in").read_text(encoding="utf-8"), "sdist must carry the video"


def test_intro_script_runs_first_and_skips_automation():
    index = (UI / "index.html").read_text(encoding="utf-8")
    assert index.index('<script src="intro.js"></script>') < index.index('src="app.js"')
    script = (UI / "intro.js").read_text(encoding="utf-8")
    for guard in ("navigator.webdriver", "sessionStorage", "prefers-reduced-motion", "'bossman.intro'", "nointro"):
        assert guard in script
    assert "setTimeout(close, 7000)" in script  # страховка от зависшего видео
