"""Autonomy page in real Chromium: empty state without 4xx/console errors; release buttons gated."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bcc.autonomy.service import AutonomyService

from .browser_support import chromium_available, reason as browser_reason
from .test_autonomy_api import GID, goal, to_user_approval
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def status(ok):
    return lambda: SimpleNamespace(ok=ok, reason="pinned" if ok else "constitution is not pinned",
                                   as_dict=lambda: {"status": "OK" if ok else "BLOCKED", "ok": ok})


def open_page(live, pw, errors, bad):  # noqa: F811
    browser = _launch(pw)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("window.__bxPreload = false;")
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    _login(page, live)
    page.goto(f"{live.url}/#/autonomy", wait_until="domcontentloaded")
    page.wait_for_function("document.getElementById('page-title').textContent === 'Автономия'", timeout=20000)
    page.wait_for_function("!document.querySelector('#view .skeleton') && "
                           "document.getElementById('view').childElementCount > 0", timeout=20000)
    return browser, page


def close(browser, page) -> None:
    """Leave the app first: an idle preload / event stream cut mid-accept on shutdown is noise."""
    try:
        page.add_init_script("window.__bxPreload = false;")
        page.goto("about:blank")
    finally:
        browser.close()


def test_empty_state_has_no_errors(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    live.svc.autonomy = AutonomyService(live.settings.data_dir, constitution_status=status(False))
    errors: list[str] = []
    bad: list[str] = []
    with sync_playwright() as pw:
        browser, page = open_page(live, pw, errors, bad)
        try:
            page.wait_for_selector("text=Целей пока нет", timeout=10000)
            assert page.locator("text=Цикл остановлен").count() == 1
        finally:
            close(browser, page)
    assert not errors, errors
    assert not bad, bad


def test_release_buttons_follow_the_gate(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    live.svc.autonomy = auto = AutonomyService(live.settings.data_dir, constitution_status=status(True))
    to_user_approval(auto)
    other = goal()
    auto.goals.create(type(other)(**{**other.__dict__, "goal_id": "JEFF-0043"}))
    errors: list[str] = []
    bad: list[str] = []
    with sync_playwright() as pw:
        browser, page = open_page(live, pw, errors, bad)
        try:
            page.wait_for_selector("text=Панель релиза", timeout=10000)
            apply = page.locator("button", has_text="Apply")
            assert apply.is_enabled()
            confirm = page.locator("button", has_text="Подтвердить выпуск")
            assert confirm.is_disabled() and confirm.get_attribute("title")
            page.click(f"text=JEFF-0043")
            page.wait_for_selector("text=JEFF-0043 · PROPOSED", timeout=10000)
            apply = page.locator("button", has_text="Apply")
            assert apply.is_disabled() and "USER_APPROVAL" in (apply.get_attribute("title") or "")
        finally:
            close(browser, page)
    assert GID and auto.goals.get(GID)["state"] == "USER_APPROVAL"       # nothing was released by rendering
    assert not errors, errors
    assert not bad, bad
