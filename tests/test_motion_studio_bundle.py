"""Motion Studio must work in the installed Windows bundle, not only in a source checkout.

Owner PC 06.10.2026, installed bundle f022c87d: GET /api/motion-studio/status ->
available=false, «инструмент tools/motion_studio не найден в этой сборке». The feature module
lives in runtime/Lib/site-packages there, so ``parents[3]/tools/motion_studio`` points nowhere,
and the builder never shipped the tool. ``make_video.py`` also reaches ``../intro_video/render.py``
for the HTML engine, so both folders travel together.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(ROOT / "tools"))

import build_windows_bundle as bw  # noqa: E402
from bcc.features import motion_studio as ms  # noqa: E402


def test_installed_bundle_finds_the_shipped_tool(tmp_path, monkeypatch):
    home = tmp_path / "BOSSMAN-Windows-x64-test"
    shipped = home / "app-support" / "motion_studio"
    shipped.mkdir(parents=True)
    (shipped / "make_video.py").write_text("# shipped\n", encoding="utf-8")
    (home / "runtime").mkdir()
    monkeypatch.delenv("BOSSMAN_MOTION_STUDIO_DIR", raising=False)
    # the module as it sits inside the bundle runtime: no tools/ folder above it
    monkeypatch.setattr(ms, "__file__", str(home / "runtime" / "Lib" / "site-packages" / "bcc" / "features" / "motion_studio.py"))
    monkeypatch.setattr(sys, "executable", str(home / "runtime" / "python.exe"))
    assert ms.tool_dir() == shipped


def test_a_source_checkout_still_uses_its_own_tool(monkeypatch):
    monkeypatch.delenv("BOSSMAN_MOTION_STUDIO_DIR", raising=False)
    assert ms.tool_dir() == ROOT / "tools" / "motion_studio"
    assert (ms.tool_dir() / "make_video.py").is_file()


def test_the_builder_ships_motion_studio_and_its_html_engine(tmp_path):
    support = tmp_path / "app-support"
    shipped = bw.install_motion_studio(support)
    assert (support / "motion_studio" / "make_video.py").is_file()
    assert (support / "motion_studio" / "epic.py").is_file()
    assert (support / "intro_video" / "render.py").is_file()
    assert any((support / "motion_studio" / "examples").glob("*.json"))
    assert not list(support.rglob("__pycache__"))
    for rel, digest in shipped["files"].items():
        assert bw.sha256_file(tmp_path / rel) == digest, rel


def test_assemble_installs_motion_studio():
    assert "install_motion_studio(" in inspect.getsource(bw.assemble)
