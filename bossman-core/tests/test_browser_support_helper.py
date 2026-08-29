"""Хелпер поиска Chromium: кроссплатформенно, без хардкода linux-путей.

Регресс на баг из аудита: на Windows жёсткий путь `/usr/bin/chromium` не
существовал, launch несуществующего бинаря ВИСЕЛ, и полный `pytest tests` не
завершался. Теперь путь спрашивается у Playwright, а его отсутствие даёт skip.
"""
from __future__ import annotations

from pathlib import Path

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
