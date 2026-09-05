"""Песочница не должна ломать саму панель: клик по элементу в превью обязан
по-прежнему выделять его, а превью — оставаться отрезанным от панели.

Проверяется в настоящем Chromium против живого сервера: атрибут sandbox и
заголовок CSP влияют на поведение браузера, а не питона, поэтому unit-тест
здесь ничего не доказывает."""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_picker_still_works_and_frame_is_isolated(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.evaluate("""async () => {
              const csrf = localStorage.getItem('bcc.csrf') || '';
              await fetch('/api/web-designer/projects', {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-BCC-CSRF': csrf},
                body: JSON.stringify({name: 'Проверка', prompt: 'кафе с доставкой', template: 'auto'})});
            }""")
            page.goto(live.url + "/#/web_designer")
            page.wait_for_selector("iframe.bd-frame", timeout=15000)

            assert page.get_attribute("iframe.bd-frame", "sandbox") == "allow-scripts"
            frame = page.frame_locator("iframe.bd-frame")
            frame.locator("h1").first.wait_for(timeout=15000)

            # кадр действительно отрезан: непрозрачный origin не даёт ни
            # localStorage панели, ни её cookie
            isolated = page.frames[1].evaluate("""async () => {
              // и localStorage, и document.cookie в песочнице БРОСАЮТ
              // SecurityError — сам бросок и есть доказательство изоляции
              let storage = 'blocked';
              try { storage = String(localStorage.getItem('bcc.csrf')); } catch (e) { storage = 'blocked'; }
              let cookie = 'blocked';
              try { cookie = String(document.cookie); } catch (e) { cookie = 'blocked'; }
              // главная проверка: сам вызов панели с cookie сессии. Песочница
              // делает origin непрозрачным, поэтому cookie не отправляется и
              // ответ — не данные владельца. location.origin при этом всё ещё
              // печатает строку URL: это не свойство безопасности, полагаться
              // на неё нельзя.
              let api = 'blocked';
              try {
                const r = await fetch('/api/agents', {credentials: 'include'});
                api = r.status === 200 ? 'AUTHENTICATED:' + (await r.text()).slice(0, 40)
                                       : 'status:' + r.status;
              } catch (e) { api = 'blocked'; }
              return {storage, cookie, api};
            }""")
            assert isolated["storage"] in ("blocked", "null"), isolated
            assert isolated["cookie"] in ("blocked", ""), isolated
            assert not str(isolated["api"]).startswith("AUTHENTICATED"), isolated

            # и при этом функция панели жива: выделение включено по умолчанию,
            # клик по элементу в песочнице доносится до инспектора через postMessage
            frame.locator("h1").first.click()
            page.wait_for_selector("text=Инспектор", timeout=10000)
            page.wait_for_function(
                "() => /\\bh1\\b/.test(document.querySelector('#view').innerText)",
                timeout=10000)
            assert errors == [], errors
        finally:
            browser.close()
