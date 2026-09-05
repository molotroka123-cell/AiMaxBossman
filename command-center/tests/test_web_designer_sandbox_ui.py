"""Песочница не должна ломать саму панель: клик по элементу в превью обязан
по-прежнему выделять его, а превью — оставаться отрезанным от панели.

Проверяется в настоящем Chromium против живого сервера: атрибут sandbox и
заголовок CSP влияют на поведение браузера, а не питона, поэтому unit-тест
здесь ничего не доказывает."""
from __future__ import annotations

import pytest
from pathlib import Path

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
            assert isolated["api"] == "blocked", isolated
            page.frames[1].evaluate("""() => parent.postMessage({source:'bd-preview', type:'select',
                el:{tag:{bad:true}, bd_id:'bd-1', classes:{}, text:{}}}, '*')""")

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


def test_create_edit_download_restore_and_reopen_through_ui(live, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(accept_downloads=True)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.goto(live.url + "/#/web_designer")
            page.get_by_placeholder('Название проекта, например «Кофейня Север»').fill("V4 UI proof")
            page.get_by_placeholder('О чём сайт и как должен выглядеть: тема, стиль, цвета…').fill("кафе с доставкой")
            page.get_by_role("button", name="Открыть проект", exact=True).click()
            editor = page.locator("textarea.bd-code")
            expect(editor).to_be_visible(timeout=20000)
            original = editor.input_value()
            source = "<!doctype HTML><html><body><h1>V4 owner source</h1><SVG viewBox='0 0 10 10'/></body></html>"
            editor.fill(source)
            with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/code")):
                editor.press("Control+s")
            frame = page.frame_locator("iframe.bd-frame")
            expect(frame.locator("h1")).to_have_text("V4 owner source")
            frame.locator("h1").click()
            inspector = page.locator(".bd-row").filter(has=page.get_by_role("button", name="Применить", exact=True))
            inspector.locator('input[type="text"]').fill("V4 selected edit")
            inspector.get_by_role("button", name="Применить", exact=True).click()
            expect(frame.locator("h1")).to_have_text("V4 selected edit")
            expect(editor).to_have_value(source.replace("V4 owner source", "V4 selected edit"))
            with page.expect_download() as download_info:
                page.get_by_role("button", name="Скачать HTML", exact=True).click()
            saved = download_info.value.path()
            assert Path(saved).read_text(encoding="utf-8") == source.replace("V4 owner source", "V4 selected edit")
            page.reload()
            expect(page.locator("textarea.bd-code")).to_have_value(source.replace("V4 owner source", "V4 selected edit"))
            versions = page.locator(".bd-vers")
            versions.filter(has=page.get_by_text("v1", exact=True)).get_by_role("button", name="Вернуть", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="Вернуть", exact=True).click()
            expect(page.locator("textarea.bd-code")).to_have_value(original)
            page.get_by_role("button", name="+ Проект", exact=True).click()
            page.get_by_placeholder('Название проекта, например «Кофейня Север»').fill("V4 second project")
            page.get_by_role("button", name="Открыть проект", exact=True).click()
            expect(page.locator("textarea.bd-code")).to_be_visible()
            second = "<html><body><h1>second project unsaved</h1></body></html>"
            page.locator("textarea.bd-code").fill(second)
            project_select = page.locator("select").filter(has=page.locator("option", has_text="V4 UI proof"))
            first_id = project_select.locator("option", has_text="V4 UI proof").get_attribute("value")
            second_id = project_select.locator("option", has_text="V4 second project").get_attribute("value")
            project_select.select_option(first_id)
            expect(page.locator("textarea.bd-code")).to_have_value(original)
            project_select.select_option(second_id)
            expect(page.locator("textarea.bd-code")).to_have_value(second)
            page.screenshot(path=str(tmp_path / "web-designer-v4-proof.png"), full_page=True)
            assert errors == [], errors
        finally:
            browser.close()
