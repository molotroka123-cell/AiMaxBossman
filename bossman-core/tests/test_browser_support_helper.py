"""Хелпер поиска Chromium: кроссплатформенно, без хардкода linux-путей.

Регресс на баг из аудита: на Windows жёсткий путь `/usr/bin/chromium` не
существовал, launch несуществующего бинаря ВИСЕЛ, и полный `pytest tests` не
завершался. Теперь путь спрашивается у Playwright, а его отсутствие даёт skip.
"""
from __future__ import annotations

from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

import browser_support


def _fresh():
    browser_support.chromium_path.cache_clear()


def test_env_var_wins_when_it_exists(tmp_path, monkeypatch):
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n")
    _fresh()
    monkeypatch.setenv("BOSSMAN_TEST_CHROMIUM", str(fake))
    assert browser_support.chromium_path() == str(fake)
    _fresh()


def test_nonexistent_env_var_does_not_win(tmp_path, monkeypatch):
    """Битый путь в переменной не должен возвращаться как «браузер есть» —
    именно он раньше уходил в launch и вешал прогон."""
    _fresh()
    monkeypatch.setenv("BOSSMAN_TEST_CHROMIUM", str(tmp_path / "nope" / "chrome"))
    monkeypatch.setattr(browser_support, "_browser_roots", lambda: [])
    monkeypatch.setattr(browser_support, "_SYSTEM_PATHS", ())

    # Тест проверяет «нет источников → None/существующий», а не сам sync-фоллбек:
    # реальный запуск sync_playwright в процессе после asyncio-тестов дедлочит
    # на Windows (см. докстринг browser_support), поэтому драйвер блокируем.
    import builtins

    real_import = builtins.__import__

    def _no_playwright(name, *a, **kw):
        if name.startswith("playwright"):
            raise ImportError("simulated: playwright missing")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_playwright)

    path = browser_support.chromium_path()
    assert path is None or Path(path).exists()
    _fresh()


def test_reports_unavailable_without_any_source(monkeypatch):
    _fresh()
    monkeypatch.delenv("BOSSMAN_TEST_CHROMIUM", raising=False)
    monkeypatch.setattr(browser_support, "_browser_roots", lambda: [])
    monkeypatch.setattr(browser_support, "_SYSTEM_PATHS", ())

    import builtins
    real_import = builtins.__import__

    def _no_playwright(name, *a, **kw):
        if name.startswith("playwright"):
            raise ImportError("simulated: playwright missing")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_playwright)
    assert browser_support.chromium_path() is None
    assert browser_support.chromium_available() is False
    assert "Chromium" in browser_support.reason()
    _fresh()


def test_available_on_this_host():
    _fresh()
    assert browser_support.chromium_available() is True   # контейнер разработки


@pytest.fixture
def installed_playwright_registry(tmp_path, monkeypatch):
    """Model installed metadata, not a running browser or successful action."""
    package = tmp_path / "playwright"
    registry = package / "driver/package"
    registry.mkdir(parents=True)
    (registry / "browsers.json").write_text(json.dumps({
        "browsers": [{"name": "chromium", "revision": "1234"}]}))
    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace(__file__=str(package / "__init__.py")))
    monkeypatch.delenv("BOSSMAN_TEST_CHROMIUM", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    _fresh()
    yield tmp_path / "browsers", registry
    _fresh()


@pytest.mark.parametrize("relative", [
    "chrome-linux64/chrome",
    "chrome-win64/chrome.exe",
    "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-linux/chrome",  # retained Linux ARM64 / older Playwright layout
    "chrome-win/chrome.exe",
])
def test_current_playwright_layout_wins_over_unrelated_system_browser(
        installed_playwright_registry, monkeypatch, relative):
    root, _ = installed_playwright_registry
    bundled = root / "chromium-1234" / relative
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"installed engine")
    system = root / "unrelated-system-chromium"
    system.write_bytes(b"system engine")
    monkeypatch.setattr(browser_support, "_browser_roots", lambda: [str(root)])
    monkeypatch.setattr(browser_support, "_SYSTEM_PATHS", (str(system),))
    assert browser_support.chromium_path() == str(bundled)


def test_only_the_installed_playwright_revision_is_selected(installed_playwright_registry, monkeypatch):
    root, _ = installed_playwright_registry
    for revision in (1234, 9999):
        binary = root / f"chromium-{revision}/chrome-linux/chrome"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"engine")
    monkeypatch.setattr(browser_support, "_browser_roots", lambda: [str(root)])
    assert browser_support.chromium_path() == str(root / "chromium-1234/chrome-linux/chrome")


def test_missing_required_revision_does_not_silently_use_stale_cache(
        installed_playwright_registry, monkeypatch):
    root, _ = installed_playwright_registry
    stale = root / "chromium-9999/chrome-linux/chrome"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"different engine")
    monkeypatch.setattr(browser_support, "_browser_roots", lambda: [str(root)])
    monkeypatch.setattr(browser_support, "_SYSTEM_PATHS", (str(stale),))
    assert browser_support.chromium_path() is None


def test_hermetic_playwright_browser_path_uses_package_registry(installed_playwright_registry, monkeypatch):
    _, registry = installed_playwright_registry
    bundled = registry / ".local-browsers/chromium-1234/chrome-linux64/chrome"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"installed engine")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert browser_support.chromium_path() == str(bundled)
