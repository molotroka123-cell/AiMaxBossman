"""Connection controls render and reach the backend without paid calls."""
import httpx
import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_connect_button_reaches_backend_and_shows_missing_key(live):
    from playwright.sync_api import sync_playwright

    # No credential is supplied: the real backend rejects before any provider call.
    with httpx.Client(base_url=live.url, trust_env=False,
                      headers={"X-BCC-Token": live.svc.auth.token}) as client:
        created = client.post("/api/providers", json={
            "name": "OpenRouter UI regression", "kind": "openai_compat",
            "base_url": "https://openrouter.ai/api/v1"})
        assert created.is_success, created.text
        provider_id = created.json()["id"]

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.goto(live.url + "/#/openrouter")
            connect = page.get_by_role("button", name="Connect", exact=True)
            connect.wait_for(timeout=10000)
            assert page.get_by_text(
                "Вставьте ключ и нажмите Connect — каталог загрузится автоматически.",
                exact=True).is_visible()
            with page.expect_response(
                    lambda r: r.url.endswith(f"/api/openrouter/{provider_id}/connect")) as response:
                connect.click()
            assert response.value.status == 422
            page.locator(".toast").filter(has_text="у провайдера нет api_key").wait_for()
            assert page.locator("#shell").is_visible()
        finally:
            browser.close()
