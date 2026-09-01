"""RunPod preflight audit: browser toolkit defaulted to HEADED Chromium.

On a headless Linux container (RunPod, any CI runner without an X server)
`_headless()` returning False meant Playwright would try to launch a real
window and fail outright — a real P1 blocker, not a style nit. Fixed by
auto-detecting a headless POSIX host via the absence of $DISPLAY, while an
explicit BOSSMAN_BROWSER_HEADLESS still wins, and the Windows dev machine
(no DISPLAY concept) keeps its prior default unchanged.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def browser_mod(monkeypatch):
    monkeypatch.delenv("BOSSMAN_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    import bossman.toolkit.browser as mod
    return importlib.reload(mod)


def test_headless_posix_no_display_defaults_true(monkeypatch, browser_mod):
    monkeypatch.setattr("os.name", "posix", raising=False)
    assert browser_mod._headless() is True


def test_headless_posix_with_display_defaults_false(monkeypatch, browser_mod):
    monkeypatch.setattr("os.name", "posix", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    importlib.reload(browser_mod)
    assert browser_mod._headless() is False


def test_headless_explicit_env_always_wins(monkeypatch, browser_mod):
    monkeypatch.setattr("os.name", "posix", raising=False)
    monkeypatch.setenv("BOSSMAN_BROWSER_HEADLESS", "1")
    importlib.reload(browser_mod)
    assert browser_mod._headless() is True

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("BOSSMAN_BROWSER_HEADLESS", "0")
    importlib.reload(browser_mod)
    assert browser_mod._headless() is False, \
        "explicit '0' must stay headed even with no DISPLAY (operator's call)"


def test_headless_non_posix_unaffected_by_display(monkeypatch, browser_mod):
    """Windows dev machine: DISPLAY is not a concept there, default unchanged."""
    monkeypatch.setattr("os.name", "nt", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    importlib.reload(browser_mod)
    assert browser_mod._headless() is False
