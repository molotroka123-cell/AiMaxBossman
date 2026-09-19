"""Правка не пропадает, если владелец перезагрузил или закрыл вкладку.

Окно автосохранения — 900 мс. Переход по боковому меню его переживает (таймер
живёт в модуле страницы), а выгрузка документа — нет: и перезагрузка, и
закрытие вкладки внутри этого окна съедали набранное молча. Замерено на
текущем дереве до починки: после `reload()` и после `close()` на сервере
оставался прежний код.

Проверяется владельческий путь: напечатать и сразу нажать F5 или закрыть
вкладку. Утверждение — про данные, а не про интерфейс.

Ограничение названо прямо и здесь, и в коде: `keepalive` не пропускает тело
больше 64 КБ платформенно, поэтому очень большой документ так не спасти. Это
сужение окна потери, а не обещание неуязвимости.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server  # noqa: F401
from .test_web_designer_recovery_ui import BASE, DRAFT, _project, _full

pytestmark = [pytest.mark.timeout(240),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]


@pytest.fixture
def live(editor_server):
    return editor_server


def test_an_edit_survives_a_reload_inside_the_autosave_window(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
            pid = _project(page, live)
            assert _full(page, pid)['code'] == BASE

            page.locator('textarea.bd-code').fill(DRAFT)
            page.reload()                       # владелец нажал F5 сразу после набора
            page.locator('textarea.bd-code').wait_for(timeout=30000)
            page.wait_for_timeout(2000)

            assert _full(page, pid)['code'] == DRAFT, (
                'перезагрузка внутри окна автосохранения съела правку владельца')
        finally:
            browser.close()


def test_an_edit_survives_closing_the_tab_inside_the_autosave_window(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            watcher = context.new_page()
            pid = _project(watcher, live)

            worker = context.new_page()
            worker.goto(live.url + '/#/web_designer')
            worker.locator('textarea.bd-code').wait_for(timeout=30000)
            closed = DRAFT.replace('OWNER DRAFT', 'CLOSED TAB WORK')
            worker.locator('textarea.bd-code').fill(closed)
            worker.close()                      # владелец закрыл вкладку сразу

            watcher.wait_for_timeout(2000)
            assert _full(watcher, pid)['code'] == closed, (
                'закрытие вкладки внутри окна автосохранения съело правку владельца')
        finally:
            browser.close()


def test_leaving_without_typing_writes_nothing(live):
    """Негативный контроль: сброс при выгрузке не должен превращаться в запись
    на каждый уход. Иначе история версий распухнет от пустых сохранений, а
    первый тест зеленел бы и от страницы, которая пишет всегда."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            watcher = context.new_page()
            pid = _project(watcher, live)
            before = _full(watcher, pid)['meta']['version']

            worker = context.new_page()
            worker.goto(live.url + '/#/web_designer')
            worker.locator('textarea.bd-code').wait_for(timeout=30000)
            worker.close()                      # ушёл, ничего не напечатав

            watcher.wait_for_timeout(1800)
            after = _full(watcher, pid)
            assert after['meta']['version'] == before, (
                'уход без правки создал версию — страница пишет на сервер без причины')
            assert after['code'] == BASE
        finally:
            browser.close()
