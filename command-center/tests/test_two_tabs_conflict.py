"""Две НАСТОЯЩИЕ вкладки над одним проектом: чья правка переживёт чужую.

Во всём наборе не было ни одного теста с двумя одновременными вкладками:
32 вхождения `new_context`/`new_page` — все одиночные. «Другую вкладку»
изображал `fetch` из той же страницы, и это изображение упускает ровно то, чем
вторая вкладка опасна: у неё СВОЁ устаревшее состояние — свой буфер редактора,
свой номер версии, свой localStorage и своя подписка на события.

Тихая потеря данных здесь выглядит как успех: владелец правит сайт в одной
вкладке, забытая вторая сохраняет своё старое содержимое поверх, и никакой
ошибки никто не видит.

Проверяется владельческий путь целиком: обе вкладки открывают один проект,
первая правит и сохраняет, вторая — та, что ничего не знает, — пытается
сохранить своё. Утверждение про данные: правка первой вкладки не исчезает
молча.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401

pytestmark = [pytest.mark.timeout(240),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

FIRST = '<!doctype html><html><body><h1>FIRST TAB WORK</h1></body></html>'
SECOND = '<!doctype html><html><body><h1>SECOND TAB STALE</h1></body></html>'


@pytest.fixture
def live(editor_server):
    return editor_server


def _open_project(page, live, name=None):
    page.goto(live.url + '/#/web_designer')
    field = page.get_by_placeholder('Название проекта, например «Кофейня Север»')
    try:
        field.wait_for(timeout=4000)
    except Exception:  # noqa: BLE001 — проект уже открыт, поля создания нет
        page.locator('textarea.bd-code').wait_for(timeout=30000)
        return int(page.evaluate("localStorage.getItem('bd.lastProject')"))
    field.fill(name or 'Две вкладки')
    page.get_by_role('button', name='Открыть проект', exact=True).click()
    page.locator('textarea.bd-code').wait_for(timeout=30000)
    return int(page.evaluate("localStorage.getItem('bd.lastProject')"))


def _server_code(page, pid) -> str:
    return page.evaluate("""async pid => (await fetch('/api/web-designer/projects/' + pid,
      {credentials: 'include'})).json()""", pid)['code']


def _save(page, code):
    editor = page.locator('textarea.bd-code')
    editor.fill(code)
    editor.press('Control+s')


def test_a_forgotten_second_tab_cannot_silently_overwrite_the_first(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            # Две независимые вкладки — своё состояние у каждой.
            first = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
            second = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
            _login(first, live)
            pid = _open_project(first, live)

            _login(second, live)
            second.goto(live.url + f'/#/web_designer?project={pid}')
            second.locator('textarea.bd-code').wait_for(timeout=30000)
            # Вторая вкладка с этого мгновения ничего не знает о первой.

            _save(first, FIRST)
            first.wait_for_timeout(1500)
            assert _server_code(first, pid) == FIRST, 'первая вкладка не сохранила свою работу'

            _save(second, SECOND)
            second.wait_for_timeout(2500)

            # Измерено на текущем дереве, а не допущено: несвежая вкладка
            # получает 409, работа первой остаётся нетронутой, а вторая об
            # отказе УЗНАЁТ. Все три условия обязательны: молчаливый отказ
            # оставил бы владельца в уверенности, что он сохранился.
            assert _server_code(first, pid) == FIRST, (
                f'забытая вторая вкладка затёрла работу первой: '
                f'{_server_code(first, pid)[:120]!r}')
            recovery = second.locator('.bd-recovery')
            assert recovery.count() == 1, 'вторая вкладка молча проглотила отказ'
            assert '409' in recovery.inner_text(), recovery.inner_text()[:200]
            assert SECOND in second.locator('textarea.bd-code').input_value(), (
                'отказ стоил владельцу его набранного текста')
        finally:
            browser.close()


def test_the_two_tabs_really_are_independent(live):
    """Негативный контроль. Если вторая вкладка на самом деле делит состояние с
    первой, весь предыдущий тест ничего не проверяет: «конфликта» не будет
    просто потому, что конфликтовать некому."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            first = browser.new_context().new_page()
            second = browser.new_context().new_page()
            _login(first, live)
            pid = _open_project(first, live)
            _login(second, live)
            second.goto(live.url + f'/#/web_designer?project={pid}')
            second.locator('textarea.bd-code').wait_for(timeout=30000)

            first.evaluate("localStorage.setItem('two-tabs-probe', 'first')")
            assert second.evaluate("localStorage.getItem('two-tabs-probe')") is None, (
                'вкладки делят localStorage — это одна и та же сессия, а не две')
        finally:
            browser.close()
