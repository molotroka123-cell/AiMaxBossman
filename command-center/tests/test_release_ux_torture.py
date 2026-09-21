"""PRE-EVO UX torture gate: real Chromium against the live Command Center.

Safe by construction: it navigates, refreshes, toggles theme/palette and reloads.
It never clicks text that can create/delete/send/run/approve an external effect.
The goal is to catch rendered-but-dead navigation, JS crashes, stale routes and
normal-owner 404/405/422/500 responses before hardware acceptance.
"""
from __future__ import annotations

import random

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [
    pytest.mark.timeout(300),
    pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
]

BAD_STATUS = {404, 405, 422, 500}
SEEDS = (1701, 42042, 99017)


def _collect(page):
    page_errors: list[str] = []
    bad_http: list[tuple[int, str, str]] = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    def response(resp):
        if resp.status in BAD_STATUS and "/api/" in resp.url:
            bad_http.append((resp.status, resp.request.method, resp.url))
    page.on("response", response)
    return page_errors, bad_http


def _assert_page_settled(page, page_id: str):
    page.wait_for_function(
        """id => location.hash.startsWith('#/' + id)
          && document.querySelector('#view')
          && !document.querySelector('#view .skeleton')""",
        arg=page_id, timeout=15000)
    text = page.locator("#view").inner_text().strip()
    assert text, f"{page_id}: visible view is empty"
    # Loading forever is a dead control even if there is no exception.
    assert "Загрузка…" not in text[:120], f"{page_id}: page remained in loading state"


def test_every_visible_navigation_item_opens_without_js_or_contract_errors(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            errors, bad_http = _collect(page)
            _login(page, live)
            page.goto(live.url + "/")
            page.wait_for_selector("#nav .nav-item", timeout=15000)
            ids = page.locator("#nav .nav-item").evaluate_all(
                "nodes => [...nodes].map(n => n.dataset.page).filter(Boolean)")
            assert len(ids) >= 10, f"navigation unexpectedly small: {ids}"
            visited = []
            for page_id in ids:
                page.locator(f'#nav .nav-item[data-page="{page_id}"]').click()
                _assert_page_settled(page, page_id)
                assert page.locator(f'#nav .nav-item[data-page="{page_id}"]').get_attribute("aria-current") == "page"
                visited.append(page_id)
            assert visited == ids
            assert errors == [], errors
            assert bad_http == [], bad_http
        finally:
            browser.close()


def test_global_controls_are_live_not_decorative(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            errors, bad_http = _collect(page)
            _login(page, live)
            page.goto(live.url + "/")
            page.wait_for_selector("#shell:not([hidden])")

            before = page.locator("html").get_attribute("data-theme")
            page.locator("#theme-toggle").click()
            after = page.locator("html").get_attribute("data-theme")
            assert after != before

            page.locator("#palette-open").click()
            page.locator("#palette:not([hidden])").wait_for()
            assert page.locator("#palette-input").is_visible()
            page.keyboard.press("Escape")
            # Waiting on '#palette[hidden]' with Playwright's default state='visible'
            # can never succeed: the selector matches precisely when the element is
            # hidden.  Verify the product effect directly instead of timing out on
            # an impossible visibility condition.
            page.locator("#palette").wait_for(state="hidden")

            page.locator("#refresh-btn").click()
            page.wait_for_function("!document.querySelector('#refresh-btn').classList.contains('spin')",
                                   timeout=5000)
            assert errors == [], errors
            assert bad_http == [], bad_http
        finally:
            browser.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_safe_random_navigation_monkey_is_reproducible(live, seed):
    from playwright.sync_api import sync_playwright

    rng = random.Random(seed)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            errors, bad_http = _collect(page)
            _login(page, live)
            page.goto(live.url + "/")
            page.wait_for_selector("#nav .nav-item", timeout=15000)
            ids = page.locator("#nav .nav-item").evaluate_all(
                "nodes => [...nodes].map(n => n.dataset.page).filter(Boolean)")
            trace = []
            for _ in range(min(18, max(10, len(ids)))):
                op = rng.choice(("navigate", "navigate", "reload", "back_forward", "theme"))
                if op == "navigate":
                    target = rng.choice(ids)
                    page.locator(f'#nav .nav-item[data-page="{target}"]').click()
                    _assert_page_settled(page, target)
                    trace.append(("navigate", target))
                elif op == "reload":
                    expected = page.evaluate("location.hash.replace(/^#\\/?/, '').split('?')[0]")
                    page.reload()
                    page.wait_for_selector("#shell:not([hidden])")
                    if expected:
                        _assert_page_settled(page, expected)
                    trace.append(("reload", expected))
                elif op == "back_forward":
                    target = rng.choice(ids)
                    page.locator(f'#nav .nav-item[data-page="{target}"]').click()
                    _assert_page_settled(page, target)
                    page.go_back(wait_until="domcontentloaded")
                    page.wait_for_selector("#shell:not([hidden])")
                    page.go_forward(wait_until="domcontentloaded")
                    page.wait_for_selector("#shell:not([hidden])")
                    trace.append(("back_forward", target))
                else:
                    old = page.locator("html").get_attribute("data-theme")
                    page.locator("#theme-toggle").click()
                    assert page.locator("html").get_attribute("data-theme") != old
                    trace.append(("theme", old))
            assert trace, f"seed {seed} executed no actions"
            assert errors == [], f"seed={seed} trace={trace} errors={errors}"
            assert bad_http == [], f"seed={seed} trace={trace} bad_http={bad_http}"
        finally:
            browser.close()
