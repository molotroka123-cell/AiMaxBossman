"""tools/site_app.py: website -> app window launcher. Only plain https, no credentials, a safe name; launcher keeps an isolated profile."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("site_app", Path(__file__).resolve().parents[2] / "tools" / "site_app.py")
site_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(site_app)


@pytest.mark.parametrize("url", ["http://higgsfield.com", "file:///C:/x", "javascript:alert(1)", "https://user:pw@higgsfield.com",
                                 "https://", "ftp://x.com"])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(SystemExit):
        site_app.validate(url, "ok")


@pytest.mark.parametrize("name", ["", "A", "../x", "a b", "x" * 60, "-a"])
def test_unsafe_names_are_refused(name):
    with pytest.raises(SystemExit):
        site_app.validate("https://higgsfield.com", name)


def test_good_input_passes_and_launcher_uses_an_isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(site_app, "BASE", tmp_path)
    monkeypatch.setattr(site_app, "edge", lambda: r"C:\edge\msedge.exe")
    url, name = site_app.validate("https://higgsfield.com", "higgsfield")
    cmd = site_app.make_launcher(url, name)
    text = cmd.read_text(encoding="utf-8")
    assert "--app=https://higgsfield.com" in text
    assert str(tmp_path / "higgsfield" / "profile") in text
    assert (tmp_path / "higgsfield" / "profile").is_dir()
