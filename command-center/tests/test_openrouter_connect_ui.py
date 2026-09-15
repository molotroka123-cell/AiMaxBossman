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
            # Тост исчезнет, а причина обязана остаться на странице: пустой
            # список без объяснения — исходная жалоба владельца.
            page.locator("#shell").get_by_text("у провайдера нет api_key").first.wait_for()
            assert page.locator("#shell").is_visible()
        finally:
            browser.close()


def test_owner_pastes_key_and_reaches_glm_in_the_catalog(live, monkeypatch):
    """Путь владельца в настоящем браузере: ключ → каталог → GLM 5.3 закреплена.

    Провайдер контролируемый (транспорт подменён в этом же процессе, где живёт
    сервер), поэтому тест доказывает ИНТЕРФЕЙС и его контракты, а не живую GLM.
    Каталог фикстуры — 262 модели с `z-ai/*` в конце алфавита: ровно та форма
    данных, на которой владелец не находил модель.
    """
    from playwright.sync_api import sync_playwright

    from .test_feat_openrouter_owner_path import GLM, patch_transport

    patch_transport(monkeypatch)

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.goto(live.url + "/#/openrouter")

            key_input = page.get_by_placeholder("sk-or-… ключ OpenRouter")
            key_input.wait_for(timeout=10000)          # форма есть БЕЗ провайдера в БД
            key_input.fill("sk-or-v1-not-a-real-key")  # ci-secret-scan: allow
            with page.expect_response(lambda r: r.url.endswith("/api/openrouter/connect")) as resp:
                page.get_by_role("button", name="Connect", exact=True).click()
            assert resp.value.status == 200, resp.value.text()

            # честный счётчик: страница не выдаёт часть каталога за весь каталог
            page.get_by_text("показано 100 из 262", exact=False).wait_for(timeout=15000)
            page.get_by_role("button", name="Показать ещё", exact=False).first.wait_for()

            search = page.get_by_placeholder("поиск по всему каталогу (например, glm)…")
            search.fill("glm")                          # поиск идёт по ВСЕМУ каталогу
            page.get_by_text(GLM, exact=True).wait_for(timeout=15000)
            page.get_by_text("показано 2 из 2", exact=False).wait_for(timeout=15000)

            with page.expect_response(lambda r: r.url.endswith("/pin")) as pinned:
                page.get_by_role("button", name="Закрепить").first.click()
            assert pinned.value.status == 200, pinned.value.text()
            page.locator(".toast").filter(has_text="Закреплена").wait_for()
        finally:
            browser.close()
