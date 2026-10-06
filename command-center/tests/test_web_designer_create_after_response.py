"""«Открыть проект»: a second click after the server answered but before the page re-rendered.

CI on 0e44365d (py3.12): the double-submit test created two projects. The guard was released in
`finally` as soon as the POST returned, while ctx.refresh() was still loading the editor, so the old,
re-enabled button took the owner's second click. Here that window is made deterministic: the reload
after the create is delayed and the second click lands inside it.
"""
from __future__ import annotations

import time

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401
from .test_double_submit_real_buttons import live, _projects  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]


def test_second_click_between_response_and_rerender_creates_nothing(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/web_designer")
            field = page.get_by_placeholder("Название проекта, например «Кофейня Север»")
            field.wait_for(timeout=30000)
            before = len(_projects(page))
            field.fill("Окно после ответа")
            created = {"n": 0}

            def route(r):
                if r.request.method == "POST":
                    created["n"] += 1
                    r.continue_()
                else:
                    if created["n"]:
                        time.sleep(1.5)        # the editor is still loading after the create answered
                    r.continue_()
            page.route("**/api/web-designer/projects**", route)
            button = page.get_by_role("button", name="Открыть проект", exact=True)
            with page.expect_response(lambda r: r.request.method == "POST" and "/api/web-designer/projects" in r.url):
                button.click()
            button.click(force=True, no_wait_after=True, timeout=3000)   # the owner clicks again
            page.wait_for_timeout(2500)
            page.unroute("**/api/web-designer/projects**")
            names = [p.get("name") for p in _projects(page)]
            assert len(names) - before == 1, names
        finally:
            browser.close()


def test_failed_create_gives_the_button_back(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/web_designer")
            field = page.get_by_placeholder("Название проекта, например «Кофейня Север»")
            field.wait_for(timeout=30000)
            field.fill("Ошибка создания")
            page.route("**/api/web-designer/projects",
                       lambda r: r.fulfill(status=500, json={"detail": "boom"}) if r.request.method == "POST" else r.continue_())
            button = page.get_by_role("button", name="Открыть проект", exact=True)
            button.click()
            page.wait_for_function("b => !b.disabled", arg=button.element_handle(), timeout=10000)
        finally:
            browser.close()
