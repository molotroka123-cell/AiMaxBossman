"""Browser smoke for the isolated Jeff presentation layer."""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [
    pytest.mark.timeout(180),
    pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
]


def test_jeff_page_is_participant_safe_and_interactive(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            requests: list[tuple[str, str]] = []
            page.on("request", lambda r: requests.append((r.method, r.url)))

            page.goto(f"{live.url}/jeff.html", wait_until="domcontentloaded")
            page.wait_for_selector(".profile-card")
            assert page.locator(".profile-card h1").inner_text() == "Jeff"
            assert "Не управляет вашим ПК" in page.locator(".profile-card").inner_text()

            computer = page.locator(".capability.locked")
            assert computer.is_disabled()
            assert "Computer Use" in computer.inner_text()

            page.click("#avatar-open")
            page.wait_for_selector("#avatar-dialog[open]")
            page.click('[data-avatar="ember"]')
            assert page.evaluate("document.documentElement.dataset.jeffAvatar") == "ember"

            page.fill("#message", "Проверка UX без запуска owner task")
            before = len(requests)
            page.click(".send")
            page.wait_for_selector(".bubble.preview")
            unsafe = [(m, u) for m, u in requests[before:] if m not in ("GET", "HEAD")]
            assert unsafe == [], unsafe
            assert "UX-preview" in page.locator(".bubble.preview").inner_text()

            assert page.locator("#voice-select").count() == 1
            assert page.locator("#voice-preview").count() == 1
        finally:
            browser.close()
