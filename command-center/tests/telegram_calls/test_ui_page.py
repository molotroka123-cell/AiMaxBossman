"""Telegram-звонки page in real Chromium against a live server on a free port and a temp data dir.

No Telegram, no worker process: the account is made "ready" by writing fake credentials into the temp data dir, and
the contacts / dial paths that need the worker are covered by the API tests with fakes.
"""
from __future__ import annotations

import pytest

from ..browser_support import chromium_available, reason as browser_reason
from ..test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

API_HASH = "0123456789abcdef0123456789abcdef"      # fixture shape only


def _open(page, live_server, errors, bad_responses):  # noqa: ANN001
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("response", lambda r: bad_responses.append((r.status, r.url)) if r.status >= 400 else None)
    _login(page, live_server)
    page.goto(live_server.url + "/#/telegram_calls")
    page.wait_for_selector("[data-testid=telegram-calls]", timeout=15000)


def test_empty_state_is_a_connect_screen_without_errors(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    errors: list[str] = []
    bad: list = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _open(page, live, errors, bad)
            page.wait_for_selector("[data-testid=calls-connect]", timeout=15000)
            assert page.inner_text("#page-title") == "Telegram-звонки"
            assert page.locator("[data-testid=calls-connect] input[type=password]").count() == 1   # api_hash is hidden
            assert page.locator("button:has-text('Позвонить')").count() == 0                       # nothing to dial yet
            page.wait_for_selector("[data-testid=calls-live]")
            assert "Событий пока нет" in page.inner_text("[data-testid=calls-live]")
            # the diagnostics button answers without a 4xx
            page.click("button:has-text('Диагностика')")
            page.wait_for_selector("[data-testid=calls-diag]:not([hidden])", timeout=15000)
        finally:
            browser.close()
    assert not errors, errors
    assert not [b for b in bad if "/api/telegram/calls" in b[1]], bad


def test_ready_account_shows_disabled_buttons_with_reasons_and_stop_resume(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    mgr = live.svc.calls_manager
    mgr.creds.save_api(123456, API_HASH)
    mgr.creds.save_session("SESSION-FIXTURE", 111)
    errors: list[str] = []
    bad: list = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _open(page, live, errors, bad)
            dial = page.locator("button:has-text('Позвонить')")
            dial.wait_for(timeout=15000)
            assert dial.is_disabled()
            assert "Разрешить звонки" in dial.get_attribute("title")
            assert "Разрешить звонки" in page.inner_text("[data-testid=dial-reason]")
            assert page.locator("button:has-text('Положить трубку')").is_disabled()
            assert page.locator("button:has-text('Продолжить')").is_disabled()
            assert page.locator("button:has-text('STOP')").is_enabled()

            page.locator("[data-testid=calls-enable] input[type=checkbox]").check(force=True)
            page.wait_for_function("() => document.querySelector('[data-testid=dial-reason]')"
                                   " && document.querySelector('[data-testid=dial-reason]').textContent.includes('второй аккаунт')",
                                   timeout=15000)
            assert page.locator("button:has-text('Позвонить')").is_disabled()          # still no confirmed peer
            assert live.svc.calls_manager.get_settings()["enabled"] is True

            page.click("button:has-text('STOP')")
            page.wait_for_function("() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Продолжить') && !b.disabled)",
                                   timeout=15000)
            assert live.svc.calls_manager.stopflag.call_stop_set()
            assert "stop звонков" in page.inner_text(".bx-pagehead-aside").lower()
            page.click("button:has-text('Продолжить')")
            page.wait_for_function("() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Продолжить') && b.disabled)",
                                   timeout=15000)
            assert not live.svc.calls_manager.stopflag.call_stop_set()
        finally:
            browser.close()
    assert not errors, errors
    assert not [b for b in bad if "/api/telegram/calls" in b[1]], bad


def test_page_is_lazy_and_registered_in_the_studio_section(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            pages = page.evaluate("window.__bxPages")
            row = next(p for p in pages if p["id"] == "telegram_calls")
            assert row["title"] == "Telegram-звонки" and row["section"] == "studio"
        finally:
            browser.close()


def test_record_audio_switch_is_disabled_with_a_title_and_other_switches_stay_live(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    mgr = live.svc.calls_manager
    mgr.creds.save_api(123456, API_HASH)
    mgr.creds.save_session("SESSION-FIXTURE", 111)
    errors: list[str] = []
    bad: list = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _open(page, live, errors, bad)
            page.wait_for_selector("[data-testid=calls-enable]", timeout=15000)
            record = page.locator("label.switch[title*='Запись звука']")
            assert record.count() == 1
            assert record.locator("input[type=checkbox]").is_disabled()
            assert page.locator("[data-testid=calls-enable] input[type=checkbox]").is_enabled()   # legit switch untouched
            assert mgr.get_settings()["record_audio"] is False
        finally:
            browser.close()
    assert not errors, errors
