"""Пустое поле на странице «Локальные инструменты» отвечает человеку.

Почему этот файл появился. Развёртка кнопок установленного продукта на
`410cb541` дала `"error": 4`, и вердикт сборки стал
`BOSSMAN_ASTRA6_FREEZE=BLOCKED` — против `OWNER_REQUIRED` на `e049adfe`,
где этой страницы ещё не было. Все четыре ошибки оказались здесь:

    Включить Qdrant            Error: Укажите папку с заметками. (oss.js:75)
    Использовать обычный поиск Error: Укажите папку с заметками.
    Обновить индекс            503 /api/memory/index; память не настроена
    Найти в заметках           Error: Введите запрос. (oss.js:100)

Причина — не текст сообщения, а способ его доставки. `memoryAction` ловит
исключение и показывает `error.message`, так что владелец нужные слова
ВИДЕЛ. Но вместе с ними уходил `console.error(err)` с объектом Error, то
есть в консоль печаталось «Error: Укажите папку…» со стеком. Развёртка
считает отказом вводу только тот случай, где запрос не отправлен и первая
строка консоли РАВНА тексту проверки; префикс имени класса и стек этому
условию не отвечают, и клик падает в общую ветку `error`.

На той же странице две кнопки — «Прочитать документ» и «Расшифровать» —
делали это правильно с самого начала: сообщение и ранний выход, без
исключения. Починка приводит остальные четыре к их образцу, а не выдумывает
новый.

Почему тест отдельный, а не строка в test_oss_ui.py: тот файл начинается с
`importorskip("docling")` и целиком пропускается там, где docling не
установлен. Здесь docling не нужен — проверяются отказы, а не движки, —
поэтому тест выполняется и в окружениях без тяжёлых зависимостей. Гейт,
который пропускается, дефект не ловит: именно так этот и прошёл.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login  # noqa: F401

pytestmark = [pytest.mark.timeout(240),
              pytest.mark.skipif(not chromium_available(), reason=reason())]

# (имя кнопки, человеческий ответ на пустое поле)
EMPTY_FIELD = [
    ('Включить Qdrant', 'Укажите папку с заметками.'),
    ('Использовать обычный поиск', 'Укажите папку с заметками.'),
    ('Обновить индекс', 'Сначала укажите папку с заметками и сохраните настройки.'),
    ('Найти в заметках', 'Введите запрос.'),
]


class _Watch:
    """Консоль, ошибки страницы и обращения к памяти за один клик."""

    def __init__(self, page):
        self.console: list[str] = []
        self.errors: list[str] = []
        self.requests: list[str] = []
        page.on('console', lambda m: m.type == 'error' and self.console.append(m.text))
        page.on('pageerror', lambda e: self.errors.append(str(e)))
        # Только ИЗМЕНЯЮЩИЕ обращения. Сама страница читает свои настройки
        # GET-запросом при отрисовке, и роутер делает это не ровно один раз;
        # считать этот GET следствием клика — значит измерять не то. Дефект
        # был в другом: пустое поле отправляло POST, обречённый на отказ.
        page.on('request', lambda r: ('/api/memory/' in r.url and r.method != 'GET'
                                      and self.requests.append(f'{r.method} {r.url}')))

    def clear(self):
        self.console.clear(); self.errors.clear(); self.requests.clear()


@pytest.fixture
def oss_page(editor_server):
    """Через процесс сервера (на приёмке — установленный архив), не в процессе теста."""
    from playwright.sync_api import sync_playwright

    server = editor_server
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1366, 'height': 900})
            watch = _Watch(page)
            login(page, server)
            page.goto(server.url + '/#/oss')
            page.get_by_role('heading', name='Локальные инструменты', exact=True).wait_for(timeout=30000)
            watch.clear()          # загрузка страницы — не результат клика
            yield page, watch, server
        finally:
            browser.close()


@pytest.mark.parametrize('label,message', EMPTY_FIELD, ids=[c[0] for c in EMPTY_FIELD])
def test_an_empty_field_is_answered_without_an_exception(oss_page, label, message):
    from playwright.sync_api import expect

    page, watch, _ = oss_page
    watch.clear()
    page.get_by_role('button', name=label, exact=True).click()

    expect(page.get_by_text(message, exact=True)).to_be_visible()
    assert not watch.errors, f'{label}: исключение дошло до страницы: {watch.errors}'
    assert not watch.console, f'{label}: в консоль ушла ошибка вместо отказа вводу: {watch.console}'
    assert not watch.requests, (
        f'{label}: пустое поле всё равно отправило изменяющий запрос {watch.requests} — '
        f'владелец получит отказ сервера вместо подсказки')


def test_a_filled_field_really_reaches_the_server(oss_page, tmp_path):
    """Негативный контроль, без которого тест выше ничего не стоит.

    «Запрос не отправлен» выполняется и для мёртвой кнопки, и для страницы,
    которая вообще не отрисовалась. Здесь то же самое действие с
    ЗАПОЛНЕННЫМ полем обязано дойти до сервера.
    """
    from playwright.sync_api import expect

    page, watch, _ = oss_page
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / 'Prague.md').write_text('# Prague\nBossman.', encoding='utf-8')

    page.get_by_label('Папка с заметками', exact=True).fill(str(notes))
    watch.clear()
    page.get_by_role('button', name='Использовать обычный поиск', exact=True).click()

    expect(page.get_by_text('Настройки сохранены. Обновите индекс перед поиском.',
                            exact=True)).to_be_visible()
    assert any(r.startswith('POST') and '/api/memory/config' in r for r in watch.requests), (
        f'заполненное поле не дошло до сервера: {watch.requests} — '
        f'значит «запроса нет» в проверках выше означает сломанную кнопку')
    assert not watch.errors, watch.errors
