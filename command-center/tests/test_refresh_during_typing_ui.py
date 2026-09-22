"""Перерисовка страницы по открытию WS не имеет права съесть то, что владелец
печатает прямо сейчас.

Механизм (BL-074). Оболочка после `state.ready` перерисовывает активную
страницу на каждом `ws.open` — и на первом подключении, и на восстановлении
(`ui/app.js`, обработчик шины). Перерисовка собирает инспектор Веб-дизайнера
заново из `state.selected`, а поле «Текст» получает значение из описания
элемента, снятого кадром ДО набора. На быстрой машине это происходит в первые
миллисекунды после загрузки и незаметно; на загруженном раннере подключение
приходит позже — и попадает между набором и нажатием «Применить». Тогда
«Применить» отправляет СТАРЫЙ текст: сервер честно принимает правку, версия
растёт, код не меняется, ошибок нет.

Здесь момент подключения задан явно: настоящий WebSocket создаётся только
после `window.__openSocket()`. Это не мок приложения — приложение то же самое,
задержана только сеть, ровно как её задерживает занятый раннер.
"""
from __future__ import annotations

import time

import pytest

from .browser_support import click_in_preview, chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401
from .test_web_designer_apply_idempotent_ui import _code, _wait_code
from .test_web_designer_recovery_ui import _project, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

TYPED = 'APPLIED WHILE CONNECTING'

# Настоящий сокет открывается по команде теста. Всё остальное — приложение как есть.
GATE = """(() => {
  const Real = window.WebSocket;
  let release = null;
  const gate = new Promise((resolve) => { release = resolve; });
  window.__openSocket = () => { if (release) release(); };
  function Gated(url, protocols) {
    const self = this;
    this.readyState = 0;
    this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
    gate.then(() => {
      const ws = new Real(url, protocols);
      self._ws = ws;
      ws.onopen = (e) => { self.readyState = 1; if (self.onopen) self.onopen(e); };
      ws.onmessage = (e) => { if (self.onmessage) self.onmessage(e); };
      ws.onclose = (e) => { self.readyState = 3; if (self.onclose) self.onclose(e); };
      ws.onerror = (e) => { if (self.onerror) self.onerror(e); };
    });
  }
  Gated.prototype.close = function () { if (this._ws) this._ws.close(); };
  Gated.prototype.send = function (data) { if (this._ws) this._ws.send(data); };
  Gated.CONNECTING = 0; Gated.OPEN = 1; Gated.CLOSING = 2; Gated.CLOSED = 3;
  window.WebSocket = Gated;
})()"""

STAMP = "() => { const page = document.querySelector('div.bx-page'); if (!page) return false; page.dataset.rerenderProbe = '1'; return true; }"
ALIVE = "() => !!document.querySelector('[data-rerender-probe=\"1\"]')"
TEXT_VALUE = """() => {
  const row = [...document.querySelectorAll('div.bd-row')].find((r) => r.textContent.includes('Текст'));
  const input = row && row.querySelector('input[type=text]');
  return input ? input.value : null;
}"""


def _replaced_within(page, budget: float = 4.0) -> bool:
    """Была ли страница пересобрана за отведённое окно.

    Окно выдерживается ЦЕЛИКОМ, когда замены нет: «сейчас ещё не заменена» и
    «не заменена вовсе» — разные утверждения, и первая редакция этого теста
    спутала их, вернувшись на первой же пробе. Перерисовка тогда приходила
    позже, уже к нажатию «Применить», и отказ выглядел как «код не изменился».
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not page.evaluate(ALIVE):
            return True
        page.wait_for_timeout(50)
    return False


def _typed_selection(page, live):
    """Проект, выбранный h1 и набранный, но ещё не применённый текст."""
    pid = _project(page, live)
    page.reload()
    page.locator('iframe.bd-frame').wait_for()
    click_in_preview(page, 'h1')
    row = page.locator('div.bd-row', has_text='Текст').first
    row.locator('input[type=text]').fill(TYPED)
    assert page.evaluate(STAMP), 'страница не отрисована — метку ставить некуда'
    return pid, row


def test_a_late_connection_does_not_eat_what_the_owner_is_typing(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script(GATE)
            pid, row = _typed_selection(page, live)

            # Подключение приходит ровно сейчас — между набором и «Применить».
            page.evaluate("() => window.__openSocket()")
            replaced = _replaced_within(page)

            assert not replaced, 'перерисовка снесла страницу, пока владелец печатал'
            assert page.evaluate(TEXT_VALUE) == TYPED, (
                f'набранный текст потерян: в поле {page.evaluate(TEXT_VALUE)!r}')

            row.get_by_role('button', name='Применить').click()
            _wait_code(page, pid, lambda code: TYPED in code)
            assert TYPED in _code(page, pid)
            assert errors == []
        finally:
            browser.close()


def test_a_late_connection_still_refreshes_when_nobody_is_typing(live):
    """Негативный контроль: отложить обновление — не значит отменить его."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.add_init_script(GATE)
            _typed_selection(page, live)
            # Владелец увёл фокус из поля — значит, обновлять можно и нужно.
            page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")

            page.evaluate("() => window.__openSocket()")
            replaced = _replaced_within(page)

            assert replaced, 'подключение не обновило страницу, хотя никто не печатал'
        finally:
            browser.close()


# --------------------------------------------------------------------------
# Найденная причина красного прогона (py3.12/py3.14, 21–22.09): страницу
# сносила не перерисовка «поверх набора», а то, что к моменту подключения
# владелец УЖЕ не печатал с точки зрения оболочки. Кадр превью повторно
# сообщал о том же выделенном элементе ('select' — повторный клик стенда или
# 'reselect' после перезагрузки кадра), инспектор пересобирался, поле с
# набранным текстом заменялось новым: текст пропадал, фокус уходил на body,
# и перерисовка по подключению честно шла дальше. На быстрой машине повторное
# сообщение приходило до набора и было незаметно.
#
# Ниже этот момент задан явно, без таймингов: повторное сообщение вызывается
# настоящей перезагрузкой кадра после автосохранения кода, а сохранение
# придержано маршрутом до конца набора.

COUNT_SELECTS = """(() => {
  window.__bdSelects = 0;
  window.addEventListener('message', (e) => {
    const d = e.data || {};
    if (d.source === 'bd-preview' && d.type === 'select') window.__bdSelects += 1;
  });
})()"""

ACTIVE_IS_TEXT = """() => {
  const a = document.activeElement;
  const row = a && a.closest && a.closest('div.bd-row');
  return !!(row && row.textContent.includes('Текст') && a.matches('input[type=text]'));
}"""


class _Hold:
    """Придержать запросы одного метода до явной команды теста; остальные — сразу."""

    def __init__(self, method: str):
        self.method = method
        self.open = False

    def handler(self, held: list):
        def handle(route):
            if self.open or route.request.method != self.method:
                route.continue_()
            else:
                held.append(route)
        return handle

    def release(self, held: list) -> None:
        self.open = True
        for route in held:
            route.continue_()


def _wait_until(page, predicate, what: str, budget: float = 15.0):
    """Ждать СОБЫТИЯ в странице; бюджет — только предохранитель от зависания."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(25)
    raise AssertionError(f'не дождались: {what}')


def test_a_repeated_selection_report_does_not_wipe_what_the_owner_is_typing(live):
    """Автосохранение кода перезагружает кадр, кадр заново описывает выделенный
    элемент — ровно в тот момент, когда владелец печатает в инспекторе."""
    from playwright.sync_api import sync_playwright
    from .test_web_designer_recovery_ui import BASE

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script(GATE)
            page.add_init_script(COUNT_SELECTS)
            pid = _project(page, live)
            page.reload()
            page.locator('iframe.bd-frame').wait_for()

            held, gate = [], _Hold('PUT')
            page.route(f'**/api/web-designer/projects/{pid}/code', gate.handler(held))
            # Правка кода взводит автосохранение (его запрос придержан).
            page.locator('textarea.bd-code').fill(BASE.replace('<p>tail</p>', '<p>tail</p><!-- draft -->'))
            click_in_preview(page, 'h1')
            row = page.locator('div.bd-row', has_text='Текст').first
            row.locator('input[type=text]').fill(TYPED)
            assert page.evaluate(ACTIVE_IS_TEXT)
            _wait_until(page, lambda: bool(held), 'автосохранение кода не ушло')

            before = page.evaluate('() => window.__bdSelects')
            gate.release(held)
            # Сохранение → перезагрузка кадра → 'ready' → 'reselect' → 'select'.
            _wait_until(page, lambda: page.evaluate('() => window.__bdSelects') > before,
                        'кадр не описал элемент заново после перезагрузки')

            assert page.evaluate(TEXT_VALUE) == TYPED, (
                f'повторное описание элемента стёрло набранное: {page.evaluate(TEXT_VALUE)!r}')
            assert page.evaluate(ACTIVE_IS_TEXT), 'фокус ушёл из поля, в котором печатает владелец'

            assert page.evaluate(STAMP)
            page.evaluate("() => window.__openSocket()")
            assert not _replaced_within(page), 'перерисовка снесла страницу, пока владелец печатал'

            row.get_by_role('button', name='Применить').click()
            _wait_code(page, pid, lambda code: TYPED in code)
            assert errors == []
        finally:
            browser.close()


def test_an_automatic_refresh_already_in_flight_waits_for_the_owner(live):
    """Проверка «печатает ли владелец» делалась только в НАЧАЛЕ перерисовки, а
    страница заменялась после сетевых запросов. Начал печатать в этом окне —
    потерял набранное. Запросы перерисовки придержаны, пока идёт набор."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.add_init_script(GATE)
            pid, row = _typed_selection(page, live)
            field = row.locator('input[type=text]')
            page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")

            held, gate = [], _Hold('GET')
            page.route('**/api/web-designer/projects', gate.handler(held))
            page.evaluate("() => window.__openSocket()")        # никто не печатает → перерисовка стартует
            _wait_until(page, lambda: bool(held), 'перерисовка по подключению не началась')

            field.fill(TYPED + ' 2')                             # владелец начал печатать посреди неё
            gate.release(held)

            assert not _replaced_within(page), 'перерисовка, начатая до набора, снесла набранное'
            assert page.evaluate(TEXT_VALUE) == TYPED + ' 2'

            # Отложить — не отменить: владелец отпустил поле, обновление приходит.
            page.evaluate("() => document.activeElement.blur()")
            assert _replaced_within(page, budget=8.0), 'отложенная перерисовка так и не состоялась'
        finally:
            browser.close()
