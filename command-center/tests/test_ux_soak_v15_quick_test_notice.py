"""«⚡ Quick Test» on the Bossman 1.5 page: missing keys are a setup notice, not an error.

Repro (ASTRA 6 installed ui-sweep, RC 1.9): on a machine without the JEV/OpenRouter keys
the endpoint answers the expected ``{"status": "OWNER_REQUIRED", "problems": [...]}``;
the page showed it through ``toastError`` (red toast + ``console.error``), so the sweep
classified the button as ``error`` and ``ui-sweep.json`` read ``not_passed``.

Expected state → a warning notice with human reasons and a clean console. A real failure
(HTTP error, unexpected status) is still a red error toast.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

KEYS = ("BOSSMAN_JEV_API_KEY", "TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "BOSSMAN_OPENROUTER_API_KEY")


def _click_quick_test(page, live):
    page.goto(live.url + "/#/v15-owner-run")
    page.wait_for_selector("#view[data-rendered='v15-owner-run']", timeout=20000)
    page.evaluate("() => { document.getElementById('toast-root').innerHTML = '' }")
    page.locator("#view button", has_text="Quick Test").first.click()
    page.wait_for_selector("#toast-root .toast", timeout=10000)
    return page.evaluate("() => [...document.querySelectorAll('#toast-root .toast')].map(t => ({cls: t.className, text: t.innerText}))")


def test_missing_keys_is_a_setup_notice_without_console_error(live, monkeypatch, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright

    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    console: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)                                    # Chromium is found via LOCALAPPDATA
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))       # then: no openrouter-test.env either
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console.append(str(e)))
        _login(page, live)
        page.wait_for_selector("#view[data-rendered]", timeout=20000)
        console.clear()
        toasts = _click_quick_test(page, live)
        browser.close()

    assert len(toasts) == 1, toasts
    assert "toast-warn" in toasts[0]["cls"] and "toast-err" not in toasts[0]["cls"], toasts
    assert "требует подготовки" in toasts[0]["text"]
    assert "нет ключа OpenRouter" in toasts[0]["text"] and "нет ключа JEV" in toasts[0]["text"]
    assert console == [], console


def test_a_real_quick_test_failure_is_still_an_error(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.wait_for_selector("#view[data-rendered]", timeout=20000)
        page.route("**/api/v15/owner-run/quick-test", lambda route: route.fulfill(
            status=500, content_type="application/json", body='{"error": {"message": "runner crashed"}}'))
        toasts = _click_quick_test(page, live)
        page.unroute("**/api/v15/owner-run/quick-test")
        page.route("**/api/v15/owner-run/quick-test", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"status": "WEIRD", "problems": []}'))
        weird = _click_quick_test(page, live)
        browser.close()

    assert any("toast-err" in t["cls"] for t in toasts), toasts
    assert any("toast-err" in t["cls"] for t in weird), weird
