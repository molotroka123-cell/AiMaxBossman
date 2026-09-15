"""Правка, брошенная внутри окна автосохранения, доходит до сервера.

Гипотеза, с которой этот файл начинался, НЕ подтвердилась, и это записано
здесь, а не спрятано. Предполагалось, что переход по боковому меню внутри
900-миллисекундного окна `SAVE_DELAY_MS` уничтожает набранное: экран
перерисовывается, редактор исчезает, час работы пропадает молча.

Измерение показало обратное. Таймер автосохранения живёт в модуле страницы, а
не в её DOM, поэтому `hashchange` его не отменяет: запрос уходит через 900 мс
после последнего нажатия клавиши, даже если владелец уже смотрит другой экран.
Первый вариант теста падал не на продукте, а на собственной гонке — он читал
DOM, пока запрос был в полёте.

Остаётся закрепить то, что верно СЕЙЧАС. Инвариант неочевиден и хрупок:
достаточно кому-то «прибраться», сняв таймер при уходе со страницы, и тихая
потеря данных появится по-настоящему. Тест держит это свойство, а не сам факт,
что страница открывается.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401
from .test_web_designer_recovery_ui import BASE, DRAFT, _project, _full

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]


@pytest.fixture
def live(editor_server):
    return editor_server


def _await_code(page, pid, expected, *, budget_ms=15000, step_ms=250):
    """Ограниченное ожидание СЕТЕВОГО запроса, который уже назначен.

    Замер на этой машине: правка доходит до сервера между 500 и 1000 мс после
    ухода (окно автосохранения — 900 мс). Бюджет в 15 с взят с запасом к
    измеренному, а не подобран под зелёный: если правка потеряна, ожидание
    кончится отказом, а не успехом.
    """
    deadline = page.evaluate('Date.now()') + budget_ms
    seen = None
    while page.evaluate('Date.now()') < deadline:
        seen = _full(page, pid)
        if seen['code'] == expected:
            return seen
        page.wait_for_timeout(step_ms)
    raise AssertionError(
        f'за {budget_ms} мс сервер так и не получил правку; последнее: '
        f'{(seen or {}).get("code", "")[:120]!r}')


def _leave_to_tasks(page):
    """Настоящий владельческий уход: клик по пункту меню, а не location.hash."""
    page.locator('#nav .nav-item[data-page="tasks"]').first.click()
    page.wait_for_function("() => !document.querySelector('textarea.bd-code')")


def test_an_edit_abandoned_inside_the_autosave_window_still_reaches_the_server(live):
    with _launch_page(live) as page:
        pid = _project(page, live)
        before = _full(page, pid)['meta']['version']
        page.locator('textarea.bd-code').fill(DRAFT)
        # Владелец уходит НЕ дожидаясь ничего: на сервере пока прежний код.
        assert _full(page, pid)['code'] == BASE
        _leave_to_tasks(page)

        # Опрос ведётся из теста, а не через wait_for_function: предикат,
        # возвращающий Promise, там истинен СРАЗУ — сам объект обещания уже
        # truthy, и ожидание кончается до ответа сервера. Такая проверка
        # зеленела бы и на потерянной правке.
        after = _await_code(page, pid, DRAFT)
        assert after['code'] == DRAFT
        assert after['meta']['version'] > before, 'сохранение обязано создать версию'


def test_leaving_without_typing_saves_nothing(live):
    """Негативный контроль: иначе первый тест зеленел бы и от страницы,
    которая пишет на сервер при каждом уходе, ничего не спрашивая."""
    with _launch_page(live) as page:
        pid = _project(page, live)
        before = _full(page, pid)
        _leave_to_tasks(page)
        page.wait_for_timeout(1500)  # заведомо больше окна SAVE_DELAY_MS
        after = _full(page, pid)
        assert after['code'] == before['code'] == BASE
        assert after['meta']['version'] == before['meta']['version'], (
            'уход без правки создал версию — страница пишет на сервер без причины')


class _launch_page:
    """Один браузер на тест: страницы здесь живут дольше одного действия."""

    def __init__(self, live):
        self.live = live

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = _launch(self._pw)
        self._context = self._browser.new_context(viewport={'width': 1440, 'height': 1000})
        return self._context.new_page()

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()
        return False
