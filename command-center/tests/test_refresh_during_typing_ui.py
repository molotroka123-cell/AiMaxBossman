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
