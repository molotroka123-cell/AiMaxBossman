"""UX sweep 30.09: regression tests for the defects the real-browser sweep found (see docs/owner/runs/UX_SWEEP_20260930.md).

Each test drives real Chromium against a live server on an empty data dir and is red on the code before the fix.
"""
from __future__ import annotations

import re

import pytest

from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401
from .browser_support import chromium_available, reason as browser_reason

pytestmark = [pytest.mark.timeout(240), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

RAW_API = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+/api/|\{\s*\"|ConnectError|\[object Object\]")


def _open(pw, live, page_id, width=1440, height=900):  # noqa: F811
    browser = _launch(pw)
    page = browser.new_page(viewport={"width": width, "height": height})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, live)
    page.goto(f"{live.url}/#/{page_id}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const v = document.querySelector('#view'); return v && v.childElementCount && !v.querySelector('.skeleton'); }",
        timeout=20000)
    page.wait_for_timeout(500)
    return browser, page, errors


def test_sidebar_section_headers_do_not_stack_on_each_other(live):  # noqa: F811
    """UX-09: sticky headers of all sections stuck to one line and drew over each other and over menu items."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "home-v3")
        assert page.evaluate("getComputedStyle(document.querySelector('.nav-section')).position") != "sticky"
        page.evaluate("document.querySelector('#nav').scrollTop = 700")
        page.wait_for_timeout(200)
        overlaps = page.evaluate("""() => {
          const r = [...document.querySelectorAll('#nav .nav-section')].map(e => e.getBoundingClientRect())
            .filter(b => b.bottom > 0 && b.top < window.innerHeight);
          let n = 0;
          for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++)
            if (Math.min(r[i].bottom, r[j].bottom) - Math.max(r[i].top, r[j].top) > 2) n++;
          return n;
        }""")
        assert overlaps == 0
        browser.close()


def test_modal_keeps_keyboard_focus_inside_and_returns_it(live):  # noqa: F811
    """UX-10: Tab left the dialog for the page under the scrim; closing lost the focus."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "agents")
        opener = page.locator("#view button", has_text="Новый агент").first
        opener.focus()
        opener.click()
        page.wait_for_selector("#modal-root .modal")
        inside = []
        for _i in range(16):
            page.keyboard.press("Tab")
            inside.append(page.evaluate("!!document.activeElement.closest('#modal-root .modal')"))
        assert all(inside), inside
        for _i in range(4):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate("!!document.activeElement.closest('#modal-root .modal')")
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-root .modal", state="detached")
        assert page.evaluate("document.activeElement && document.activeElement.textContent.includes('Новый агент')")
        browser.close()


def test_models_check_all_is_disabled_with_a_reason_when_there_are_no_models(live):  # noqa: F811
    """UX-01: «Проверить все» on an empty registry was a silent no-op."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "models")
        button = page.locator("#view button", has_text="Проверить все").first
        assert button.is_disabled()
        assert "моделей ещё нет" in (button.get_attribute("title") or "")
        browser.close()


def test_home_create_agent_opens_the_new_agent_dialog_directly(live):  # noqa: F811
    """UX-03: the button only navigated to «Агенты» and needed a second click."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "home-v3")
        page.locator("#view button", has_text="Создать агента").first.click()
        page.wait_for_selector("#modal-root .modal h2", timeout=10000)
        assert page.locator("#modal-root .modal h2").first.inner_text().strip() == "Новый агент"
        browser.close()


def test_home_remove_attachments_button_only_shows_when_there_is_something_to_remove(live):  # noqa: F811
    """UX-02: the button was always visible and did nothing without attachments."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "home-v3")
        remove = page.locator("#view button", has_text="Убрать вложения")
        assert remove.count() == 1 and not remove.first.is_visible()
        page.set_input_files("#view input[type=file]", files=[{"name": "a.srt", "mimeType": "text/plain", "buffer": b"1"}])
        page.wait_for_timeout(200)
        assert remove.first.is_visible()
        remove.first.click()
        assert not remove.first.is_visible()
        browser.close()


def test_apps_refused_start_and_policy_text_have_no_api_call_or_json(live):  # noqa: F811
    """UX-08: the refusal hint read «включить — PUT /api/apps/control/policy {"enabled": true}»."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "apps")
        assert not RAW_API.search(page.locator("#view").inner_text())
        start = page.locator("#view button", has_text=re.compile(r"^\s*Запустить\s*$")).first
        start.click()
        page.wait_for_selector("#toast-root .toast-hint", timeout=10000)
        hint = page.locator("#toast-root .toast-hint").first.inner_text()
        assert "Разрешить запуск приложений" in hint and not RAW_API.search(hint), hint
        check = page.locator("#view button", has_text="Проверить состояние").first
        check.click()
        page.wait_for_selector("#toast-root .toast-msg:has-text('Состояние приложений обновлено')", timeout=15000)
        browser.close()


def test_overview_activity_shows_human_labels_not_raw_json(live):  # noqa: F811
    """UX-11: «Обзор» printed event kinds and {"enabled":false,...} payloads."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "home-v3")
        page.evaluate("""async () => {
          const csrf = localStorage.getItem('bcc.csrf') || '';
          await fetch('/api/apps/control/policy', {method: 'PUT', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-BCC-CSRF': csrf}, body: JSON.stringify({enabled: false})});
        }""")
        page.goto(f"{live.url}/#/overview", wait_until="domcontentloaded")
        page.wait_for_function("document.querySelector('#view').dataset.rendered === 'overview'", timeout=20000)
        page.wait_for_timeout(500)
        text = page.locator("#view").inner_text()
        assert "apps.control_policy_changed" not in text
        assert not RAW_API.search(text), text[:600]
        browser.close()


def test_command_page_shows_a_human_reason_when_opencode_is_not_running(live):  # noqa: F811
    """UX-05: the page printed «/api/info: ConnectError; /api/session: ConnectError; …»."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "command")
        text = page.locator("#view").inner_text()
        assert "ConnectError" not in text
        assert "OpenCode" in text
        browser.close()


def test_coding_session_dialog_asks_for_the_repository_before_calling_the_server(live):  # noqa: F811
    """UX-13: an empty form went to the server and came back as 400 «source_repo обязателен»."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page, _ = _open(pw, live, "coding")
        statuses: list[int] = []
        page.on("response", lambda r: statuses.append(r.status) if "/api/coding-sessions" in r.url and r.request.method == "POST" else None)
        page.locator("#view button", has_text="Новая сессия").first.click()
        page.wait_for_selector("#modal-root .modal")
        page.locator("#modal-root .modal button", has_text=re.compile(r"^\s*Создать\s*$")).click()
        page.wait_for_selector("#toast-root .toast-msg:has-text('Укажите папку репозитория')", timeout=10000)
        assert statuses == []
        browser.close()
