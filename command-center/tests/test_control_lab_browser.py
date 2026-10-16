"""Control Lab rendered in Windows Chromium against the isolated test backend."""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_control_lab_live_demo_tree_and_roadmap_are_read_only(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    writes: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.add_init_script("window.__bxPreload = false;")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: writes.append(f"{request.method} {request.url}")
                if "/api/" in request.url and not request.url.endswith("/api/login")
                and request.method not in {"GET", "HEAD"} else None)
        try:
            _login(page, live)
            page.goto(f"{live.url}/#/control-lab", wait_until="domcontentloaded")
            page.get_by_role("heading", name="Control Lab", exact=True).wait_for(timeout=20000)
            page.get_by_role("button", name="Mission Control", exact=True).wait_for()
            page.get_by_text("LIVE · API чтение", exact=False).wait_for()
            page.get_by_role("button", name="Capability Tree", exact=True).click()
            page.get_by_text("Capability Tree · реестр и evidence", exact=True).wait_for()
            assert page.locator(".cl-tree-row").count() <= 60
            page.locator("#view .cl-tabs").get_by_role("button", name="Roadmap Simulator", exact=True).click()
            page.get_by_text("Roadmap Simulator · до 01.01.2027", exact=True).wait_for()
            page.get_by_role("slider", name="Плановый бюджет в долларах").evaluate(
                "el => { el.value = '500'; el.dispatchEvent(new Event('change', {bubbles:true})); }")
            page.get_by_text("Плановый бюджет · $500", exact=True).wait_for()
            page.locator("#view .cl-mode").get_by_role("button", name="DEMO", exact=True).click()
            page.get_by_text("DEMO: все показатели ниже синтетические примеры", exact=False).wait_for()
            page.locator("#view .cl-tabs").get_by_role("button", name="Capability Tree", exact=True).click()
            assert page.get_by_text("Демо-строка, не подтверждение работы.", exact=False).count() == 3
            assert not writes, writes
        finally:
            page.goto("about:blank")
            browser.close()
    assert not errors, errors
