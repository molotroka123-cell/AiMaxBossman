"""Неверный ввод отвечает человеку, а не трейсбеком.

Пустые состояния в наборе покрыты хорошо, а НЕВЕРНЫЙ ввод — почти нет: ни
отрицательного размера, ни значения за пределом, ни «это вообще не документ».
Между тем именно так владелец и ошибается: опечатка в поле, вставленный не тот
текст, число не из того диапазона.

Требование простое и проверяется здесь буквально:

  * отказ обязан быть ЧЕТЫРЁХСОТЫМ, а не пятисотым — пятисотый означает, что
    ввод владельца уронил сервер;
  * в ответе обязано быть непустое человеческое сообщение;
  * наружу не должно уходить ни трейсбека, ни имени исключения Python —
    владельцу это ничего не объясняет, а злоумышленнику рассказывает о
    внутренностях.

Негативный контроль обязателен: без него «везде 4xx» могло бы означать, что
ручка сломана или недоступна, и тест был бы пуст.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

LEAKS = ('Traceback', 'File "/', 'ValidationError', 'KeyError', 'AttributeError',
         'sqlalchemy', 'pydantic_core')

# (имя, метод, путь, тело) — настоящие ручки и настоящие ошибки владельца.
BAD_INPUT = [
    ('проект без шаблона строкой', 'POST', '/api/web-designer/projects',
     {'name': 'x', 'prompt': '', 'template': None, 'palette': ''}),
    ('картинка шириной в один пиксель', 'POST', '/api/images/jobs',
     {'prompt': 'x', 'model_alias': 'mock-image', 'width': 1}),
    ('картинок больше, чем разрешено', 'POST', '/api/images/jobs',
     {'prompt': 'x', 'model_alias': 'mock-image', 'count': 99}),
    ('пустой запрос картинки', 'POST', '/api/images/jobs',
     {'prompt': '', 'model_alias': 'mock-image'}),
    ('отрицательное число шагов', 'POST', '/api/images/jobs',
     {'prompt': 'x', 'model_alias': 'mock-image', 'steps': -5}),
]

GOOD_INPUT = [
    ('проект с правильным шаблоном', 'POST', '/api/web-designer/projects',
     {'name': 'контроль', 'prompt': '', 'template': '', 'palette': ''}),
    ('картинка в границах', 'POST', '/api/images/jobs',
     {'prompt': 'контроль', 'model_alias': 'mock-image'}),
]


@pytest.fixture
def live(editor_server):
    return editor_server


def _send(page, method: str, path: str, body: dict) -> dict:
    return page.evaluate("""async ([method, path, body]) => {
      const r = await fetch(path, {method, credentials: 'include',
        headers: {'Content-Type': 'application/json',
                  'X-BCC-CSRF': localStorage.getItem('bcc.csrf')},
        body: JSON.stringify(body)});
      return {status: r.status, text: (await r.text()).slice(0, 1200)};
    }""", [method, path, body])


@pytest.fixture
def page(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_context(viewport={'width': 1440, 'height': 1000}).new_page()
            _login(page, live)
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize('name,method,path,body', BAD_INPUT, ids=[c[0] for c in BAD_INPUT])
def test_a_wrong_value_is_refused_with_a_human_sentence(page, name, method, path, body):
    reply = _send(page, method, path, body)
    assert 400 <= reply['status'] < 500, (
        f'{name}: ввод владельца дал {reply["status"]} — это отказ сервера, а не отказ вводу; '
        f'{reply["text"][:300]}')
    assert reply['text'].strip(), f'{name}: отказ без единого слова объяснения'
    leaked = [marker for marker in LEAKS if marker in reply['text']]
    assert not leaked, f'{name}: наружу ушли внутренности {leaked}: {reply["text"][:300]}'
    # Человеческое сообщение — это буквы, а не только имена полей и кавычки.
    assert sum(ch.isalpha() for ch in reply['text']) > 10, reply['text'][:300]


@pytest.mark.parametrize('name,method,path,body', GOOD_INPUT, ids=[c[0] for c in GOOD_INPUT])
def test_the_same_endpoint_accepts_a_correct_value(page, name, method, path, body):
    """Негативный контроль: иначе «везде 4xx» означало бы сломанную ручку."""
    reply = _send(page, method, path, body)
    assert reply['status'] < 300, f'{name}: правильный ввод отвергнут — {reply}'


def test_the_leak_detector_is_not_inert():
    """Страж, который ни на чём не срабатывает, охраняет только сам себя.

    Проверки выше зелёные потому, что утечек нет, — и выглядели бы точно так же,
    если бы список маркеров опустел или был переименован. Здесь он проверяется
    на образце настоящего ответа с трейсбеком.
    """
    leaky = ('Traceback (most recent call last):\n'
             '  File "/app/bcc/features/images.py", line 348, in create_job\n'
             '    raise KeyError("model_alias")\n'
             'pydantic_core._pydantic_core.ValidationError: 1 validation error')
    assert [marker for marker in LEAKS if marker in leaky], (
        'ни один маркер не сработал на заведомо протёкшем ответе — '
        'значит проверка утечек ничего не проверяет')
    clean = '{"error":{"message":"неверный запрос: ширина не меньше 256","hint":"проверьте поля"}}'
    assert not [marker for marker in LEAKS if marker in clean], (
        'маркер сработал на нормальном человеческом отказе — проверка будет врать')
