"""Browser acceptance of the «Telegram-звонки» panel against a real backend (offline test mode, no Telegram).

Empty state: no console errors, no 4xx/5xx from the panel, every disabled button says why (title). Then the owner path through
the real controls: connect (credentials, code) -> pick the second account with a confirmation -> allow calls -> dial -> STOP.
"""
from __future__ import annotations

import re

import pytest

from ..browser_support import chromium_available, reason as browser_reason
from ..test_ux2_thinking_pane import _launch
from .test_calls_e2e_offline import (API_HASH, API_ID, CODE, PEER_ID, PHONE, api, backend, status, until)  # noqa: F401

pytestmark = [
    pytest.mark.timeout(240),
    pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
]

CALLS = "/api/telegram/calls"


def _open_panel(pw, backend):
    browser = _launch(pw)
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 and "/api/" in r.url else None)
    page.goto(backend.url + "/#/telegram_calls")
    page.wait_for_selector("#login-token", timeout=20000)
    page.fill("#login-token", backend.token)
    page.click("#login-submit")
    page.wait_for_function("() => !document.querySelector('#login-token') || document.querySelector('#login-token').offsetParent === null",
                           timeout=20000)
    page.goto(backend.url + "/#/telegram_calls")
    problems.clear()                                    # the login itself is not the panel's business
    return browser, page, problems


def _panel_text(page) -> str:
    return page.evaluate("() => document.body.innerText")


def test_empty_state_has_no_errors_and_every_disabled_button_explains_itself(backend):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, problems = _open_panel(pw, backend)
        try:
            page.wait_for_function("() => /Telegram-звонки/.test(document.body.innerText)", timeout=20000)
            page.wait_for_timeout(2500)                 # a status poll or two
            assert problems == [], problems
            text = _panel_text(page)
            assert "звонк" in text.lower()
            assert not re.search(r"undefined|\[object Object\]|NaN", text), text[:800]
            silent = page.evaluate("""() => [...document.querySelectorAll('button[disabled]')]
                .filter((b) => b.offsetParent !== null && !(b.title || b.getAttribute('aria-label') || '').trim())
                .map((b) => b.innerText.trim() || b.id || b.className)""")
            assert silent == [], f"disabled buttons without a reason: {silent}"
            dial = page.get_by_role("button", name=re.compile("Позвонить"))
            assert dial.count() >= 1 and dial.first.is_disabled(), "no call can be placed on an empty panel"
            assert status(backend)["call"] is None
        finally:
            browser.close()


def test_the_panel_follows_the_owner_path_and_stop_wins(backend):
    from playwright.sync_api import sync_playwright

    assert api(backend, "POST", f"{CALLS}/credentials", json={"api_id": API_ID, "api_hash": API_HASH}).status_code == 200
    assert api(backend, "POST", f"{CALLS}/login/start", json={"phone": PHONE}).status_code == 200
    assert api(backend, "POST", f"{CALLS}/login/code", json={"code": CODE}).json()["state"] == "ready"
    assert api(backend, "PUT", f"{CALLS}/peer", json={"user_id": PEER_ID, "confirm": True}).status_code == 200
    assert api(backend, "PUT", f"{CALLS}/settings", json={"enabled": True}).status_code == 200
    with sync_playwright() as pw:
        browser, page, problems = _open_panel(pw, backend)
        try:
            dial = page.get_by_role("button", name=re.compile("Позвонить"))
            page.wait_for_function("() => /Позвонить/.test(document.body.innerText)", timeout=20000)
            until(lambda: dial.first.is_enabled() or None, timeout=20, what="the dial button enabled")
            dial.first.click()
            until(lambda: (status(backend)["call"] or None), timeout=40, what="the call started by the panel button")
            assert len(backend.workers()) == 1
            page.get_by_role("button", name=re.compile("^\\s*STOP", re.I)).first.click()
            until(lambda: (lambda s: s if s["call"] is None and (s.get("last_call") or {}).get("outcome") == "stopped" else None)(status(backend)),
                  timeout=40, what="the call ended by STOP")
            assert status(backend)["stop"]["call"] is True
            page.wait_for_timeout(1500)
            assert dial.first.is_disabled(), "no redial from the panel after STOP"
            assert problems == [], problems
        finally:
            browser.close()
            api(backend, "POST", f"{CALLS}/resume")
