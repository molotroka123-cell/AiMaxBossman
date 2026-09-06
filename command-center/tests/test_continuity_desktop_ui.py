"""Desktop launchers use the real router and respect owner theme preferences."""
import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_desktop_navigation_theme_and_mobile(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            assert page.locator('html').get_attribute('data-theme') == 'light'
            dock = page.locator('#desktop-dock')
            assert dock.is_visible()
            dock.locator('[data-page="web_designer"]').click()
            page.wait_for_url('**/#/web_designer')
            assert dock.locator('[data-page="web_designer"]').get_attribute('aria-current') == 'page'
            # Skip-to-content must focus the existing view, never change the hash route.
            page.locator('#desktop-skip').focus()
            page.keyboard.press('Enter')
            assert page.url.endswith('/#/web_designer')
            assert page.evaluate('document.activeElement.id') == 'view'
            page.locator('#theme-toggle').click()
            page.reload()
            page.locator('#shell:not([hidden])').wait_for()
            assert page.locator('html').get_attribute('data-theme') == 'dark'
            page.set_viewport_size({"width": 390, "height": 844})
            assert not dock.is_visible()
            assert page.locator('#mobilenav').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()
