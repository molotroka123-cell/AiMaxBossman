"""#/agents?new=1 opens the «new agent» window only while the owner is still on Agents.

Found by the CI pages sweep (2026-10-06): home-v3 «Создать агента» -> #/agents?new=1, the sweep went on at once,
and the window opened late on top of Chat/Apps and swallowed the next click («Новая модель», 30 s timeout).
"""
from __future__ import annotations

import time

import pytest

from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401
from .browser_support import chromium_available, reason as browser_reason

pytestmark = [pytest.mark.timeout(120), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _open_agents_then(page, url, leave_to):
    def slow(route):
        time.sleep(1.0)          # the agents list arrives after the owner already left
        route.continue_()
    page.route("**/api/agents*", slow)
    page.goto(f"{url}/#/agents?new=1", wait_until="domcontentloaded")
    if leave_to:
        page.evaluate("h => { location.hash = h; }", leave_to)
    page.wait_for_timeout(2500)
    page.unroute("**/api/agents*")
    return page.evaluate("() => document.querySelectorAll('#modal-root .modal').length")


def test_new_agent_window_does_not_follow_the_owner_to_another_page(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            assert _open_agents_then(page, live.url, "#/chat") == 0
            # negative control: staying on Agents still opens the window (UX-03 contract)
            assert _open_agents_then(page, live.url, None) == 1
            # the window opened on Agents does not travel with the owner to the next page (hash route, no reload)
            page.evaluate("() => { location.hash = '#/chat'; }")
            page.wait_for_function("() => !document.querySelector('#modal-root .modal')", timeout=5000)
            # a window opened for the new page itself is kept: same-page query change does not close it
            page.goto(f"{live.url}/#/agents", wait_until="domcontentloaded")
            page.wait_for_selector("#view [data-rendered], #view .bx-page", timeout=20000)
            page.get_by_role("button", name="Новый агент").first.click()
            page.wait_for_selector("#modal-root .modal", timeout=10000)
            page.evaluate("() => { location.hash = '#/agents?x=1'; }")
            page.wait_for_timeout(500)
            assert page.evaluate("() => document.querySelectorAll('#modal-root .modal').length") == 1
        finally:
            browser.close()


def test_window_does_not_open_after_leaving_during_its_own_models_fetch(live):  # noqa: F811
    """openAgentModal re-fetches an empty model list; the owner may leave while that fetch is in flight."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            # No /api/models of the shell may still be in flight: api.js joins concurrent identical GETs, so the page render
            # would join that earlier request (which this test's route never sees) and the window's own re-fetch would become
            # call #1 instead of #2. Found by tracing the page's fetch calls: 1 vs 2 routed calls depended on this timing.
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            calls = {"n": 0}
            held = []

            def models(route):
                calls["n"] += 1
                if calls["n"] == 1:   # the page render: empty list, fast
                    route.fulfill(status=200, content_type="application/json", body='{"models": []}')
                else:                 # the window's own re-fetch: held until the owner has left
                    held.append(route)
            page.route("**/api/models*", models)
            page.goto(f"{live.url}/#/agents?new=1", wait_until="domcontentloaded")
            for _ in range(200):      # wait for the re-fetch to be in flight (processes route events)
                if held:
                    break
                page.wait_for_timeout(100)
            page.evaluate("() => { location.hash = '#/chat'; }")
            page.wait_for_timeout(300)
            for route in held:
                route.continue_()
            page.wait_for_timeout(1500)
            page.unroute("**/api/models*")
            assert calls["n"] >= 2, "the race window was not exercised"
            assert page.evaluate("() => document.querySelectorAll('#modal-root .modal').length") == 0
        finally:
            browser.close()
