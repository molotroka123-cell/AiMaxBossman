"""Двойное нажатие настоящих мутирующих кнопок — со счётом созданных объектов.

Барьер от двойной отправки в репозитории проверялся только на СИНТЕТИЧЕСКОЙ
кнопке, смонтированной внутри самого теста. Ни одна продуктовая кнопка так не
проверялась, а владелец жмёт именно их — и жмёт дважды, когда первый клик
выглядит беззвучным.

Барьер к тому же неочевиден: `btn` из `pages/_ui.js` ставит признак занятости
ПОСЛЕ вызова обработчика, то есть запрос к этому моменту уже ушёл. Держится он
на том, что обработчик синхронно доходит до первого `await` и возвращает
обещание в том же такте, а второй клик владельца приходит в следующем. Это
рассуждение, и оно легко ломается «упрощением» — поэтому здесь не рассуждение,
а счёт созданных объектов.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]


@pytest.fixture
def live(editor_server):
    return editor_server


def _projects(page) -> list[dict]:
    return page.evaluate("""async () => {
      const r = await fetch('/api/web-designer/projects', {credentials: 'include'});
      const body = await r.json();
      return Array.isArray(body) ? body : (body.items || body.projects || []);
    }""")


def test_double_clicking_open_project_creates_exactly_one_project(live):
    """Владелец жмёт «Открыть проект» дважды подряд, как по обычной кнопке."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
            _login(page, live)
            page.goto(live.url + "/#/web_designer")
            field = page.get_by_placeholder("Название проекта, например «Кофейня Север»")
            field.wait_for(timeout=30000)
            field.fill("Двойное нажатие")
            before = len(_projects(page))

            button = page.get_by_role("button", name="Открыть проект", exact=True)
            button.click()
            button.click(force=True, no_wait_after=True)   # второй клик владельца
            page.locator("textarea.bd-code").wait_for(timeout=30000)

            after = _projects(page)
            created = len(after) - before
            assert created == 1, (
                f"двойное нажатие создало проектов: {created}; "
                f"названия: {[p.get('name') for p in after]}")
        finally:
            browser.close()


def test_a_dropped_guard_would_be_caught(live):
    """Позитивный контроль к первому тесту: тот же двойной клик БЕЗ барьера
    обязан создавать два проекта. Иначе первый тест зеленел бы и на сломанном
    барьере — просто потому, что второй клик не доходит до обработчика."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
            _login(page, live)
            page.goto(live.url + "/#/web_designer")
            page.get_by_placeholder("Название проекта, например «Кофейня Север»").wait_for(timeout=30000)
            before = len(_projects(page))
            # Тот же запрос, что шлёт кнопка, дважды и без всякого барьера.
            page.evaluate("""async () => {
              const body = {name: 'Контроль без барьера', prompt: '', template: '', palette: ''};
              const headers = {'Content-Type': 'application/json',
                               'X-BCC-CSRF': localStorage.getItem('bcc.csrf')};
              await Promise.all([
                fetch('/api/web-designer/projects', {method: 'POST', credentials: 'include',
                                                     headers, body: JSON.stringify(body)}),
                fetch('/api/web-designer/projects', {method: 'POST', credentials: 'include',
                                                     headers, body: JSON.stringify(body)}),
              ]);
            }""")
            created = len(_projects(page)) - before
            assert created == 2, (
                "два одинаковых запроса создали не два проекта — значит счёт "
                f"ничего не различает и первый тест бессмысленен; создано: {created}")
        finally:
            browser.close()
