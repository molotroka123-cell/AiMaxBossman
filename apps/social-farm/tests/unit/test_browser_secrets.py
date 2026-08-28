"""Секрет не должен покидать страницу. Проверяется опытом, а не обещанием.

Каждый тест здесь кладёт ИЗВЕСТНУЮ строку туда, где секрет оказывается в
жизни, и ищет её во всём, что приложение отдало наружу. Не «поле помечено
secret», не «редакция вызвана» — именно отсутствие строки в выводе.

Четыре пути, проверяемые ниже, — это ровно те четыре, что однажды нашлись
РАБОЧИМИ в браузере плоскости управления (`command-center/bcc/v2/browser_control.py`,
`tests/test_v22_browser_security.py`) в коде, который считался безопасным.
Приложение самостоятельное и ничего оттуда не импортирует; повторяется урок,
а не код.
"""
from __future__ import annotations

import html
import urllib.parse

import pytest

from browser_kit import (CSRF_TOKEN, OTP_CODE, PASSWORD, flatten, login_page,
                         login_session)
from social_farm.browser import Redactor, SecretRef, redact_secrets
from social_farm.browser.secrets import MASK


# ------------------------------------------------------- значение поля в снимке

async def test_password_field_value_does_not_reach_the_snapshot():
    """Первая линия: значение `type=password` не покидает страницу вовсе."""
    dom, sess = login_session()
    snapshot = await sess.snapshot()
    assert PASSWORD not in flatten(snapshot.to_dict())


async def test_hidden_field_value_does_not_reach_the_snapshot():
    """Токен в `type=hidden` — секрет, которого признак `type=password` не видит.

    Значение CSRF/сессионного токена не нужно ни для одного сценария: чтобы
    отправить форму, достаточно нажать кнопку, поле отправит браузер сам.
    """
    dom, sess = login_session()
    snapshot = await sess.snapshot()
    assert CSRF_TOKEN not in flatten(snapshot.to_dict())


async def test_password_in_a_text_field_does_not_reach_the_snapshot():
    """Пароль в `type=text` с `autocomplete=current-password`.

    Так делают старые формы входа и формы с кнопкой «показать пароль». Тип поля
    здесь ничего не говорит о содержимом, а `autocomplete` говорит прямо.
    """
    dom, sess = login_session()
    snapshot = await sess.snapshot()
    assert PASSWORD not in flatten(snapshot.to_dict())


async def test_one_time_code_does_not_reach_the_snapshot():
    """Код из SMS. Одноразовый — но ровно до тех пор, пока им не воспользовались."""
    dom, sess = login_session()
    snapshot = await sess.snapshot()
    assert OTP_CODE not in flatten(snapshot.to_dict())


async def test_page_snapshot_still_says_that_the_field_is_filled():
    """Прятать значение — не значит слепить вызывающего.

    Без признака «заполнено» невозможно понять, нужен ли ввод, и появился бы
    соблазн прочитать значение «просто чтобы проверить».
    """
    dom, sess = login_session()
    snapshot = await sess.snapshot()
    secret_fields = [e for e in snapshot.elements if e.get("secret")]
    assert len(secret_fields) >= 4, "секретными признаны не все опасные поля"
    assert all(e["filled"] for e in secret_fields if e["type"] != "text" or True)


# ------------------------------------------------------------- секрет в адресе

async def test_password_does_not_reach_the_url_after_a_get_form_submit():
    """Форма входа с `method=GET` уносит пароль в адрес.

    Это не выдумка ради теста: `method=GET` на форме входа встречается, и тогда
    пароль оказывается в адресной строке, в истории браузера и в каждом снимке.
    Адрес идёт наружу тремя дорогами — снимок, результат действия, аудит, — и
    проверяются все три.
    """
    dom, sess = login_session(secrets={"vault://login": PASSWORD})
    await sess.start()
    assert sess.state.value == "LOGIN_REQUIRED"
    await sess.assist_fill_secret("login.password.fill", SecretRef("vault://login"))

    # Человек нажал «Войти»; браузер ушёл на адрес с параметрами формы.
    dom.page.url = (f"https://fixture.local/accounts/login/?username=nashe_ateljie"
                    f"&password={urllib.parse.quote(PASSWORD)}")

    snapshot = await sess.snapshot()
    assert PASSWORD not in flatten(snapshot.to_dict()), "пароль уехал в адресе снимка"
    assert urllib.parse.quote(PASSWORD) not in flatten(snapshot.to_dict())

    written = sess.audit.dicts(sess.redactor)
    assert PASSWORD not in flatten(written)
    assert urllib.parse.quote(PASSWORD) not in flatten(written)


async def test_password_does_not_reach_the_result_of_an_action():
    """Результат действия — это то, что вызывающий получает и логирует чаще всего."""
    dom, sess = login_session(secrets={"vault://login": PASSWORD})
    await sess.start()
    await sess.assist_fill_secret("login.password.fill", SecretRef("vault://login"))
    dom.page.url = f"https://fixture.local/login/?password={PASSWORD}"
    sess._state = sess._state.__class__.TAKEOVER_REQUIRED  # человек у экрана
    result = await sess.assist_fill_text("login.username.fill", "nashe_ateljie")
    assert PASSWORD not in flatten(result)
    assert PASSWORD not in flatten(sess.describe())


# ------------------------------------------- редакция переживает искажение строки

@pytest.mark.parametrize("distortion,make", [
    ("схлопнутые пробелы", lambda s: s.replace("-", " \t ").replace("  ", " ")),
    ("percent-кодирование", lambda s: urllib.parse.quote(s, safe="")),
    ("percent-кодирование формы", lambda s: urllib.parse.quote_plus(s)),
    ("HTML-экранирование", lambda s: html.escape(s)),
])
def test_redaction_survives_distortion(distortion, make):
    """Секрет по дороге наружу успевает измениться, оставаясь секретом.

    Снимок схлопывает пробелы, адрес приезжает percent-кодированным, разметка —
    экранированной. Редакция по точному совпадению пропускает все три формы.
    """
    secret = "parol s probelom i-defisom 2026"
    redactor = Redactor([secret])
    distorted = make(secret)
    cleaned = redactor.text(f"на странице: {distorted}")
    assert distorted not in cleaned, f"{distortion}: секрет прошёл редакцию"
    assert MASK in cleaned


def test_redaction_survives_whitespace_collapse_of_a_tabbed_secret():
    """Пароль с табуляцией: снимок схлопнет её в пробел, и точное совпадение отпадёт."""
    secret = "parol\tс\tтабуляцией-2026"
    collapsed = "parol с табуляцией-2026"
    cleaned = Redactor([secret]).text(f"поле: {collapsed}")
    assert collapsed not in cleaned
    assert MASK in cleaned


def test_redaction_survives_truncation():
    """Обрезка ломает точное совпадение, но не ломает секрет.

    Снимок режет текст, аудит режет описание — и наружу уезжает префикс. Ключ
    API, обрезанный на десять символов, остаётся ключом API.
    """
    secret = "sf-live-01234567890123456789abcdefghij"
    truncated = secret[:24]
    cleaned = Redactor([secret]).text(f"ключ: {truncated} …")
    assert truncated not in cleaned
    assert MASK in cleaned


def test_redaction_survives_case_folding():
    """`semantic_identity` приводит текст к нижнему регистру — редакция обязана
    пережить и это."""
    secret = "SekretnyjParol-2026"
    cleaned = Redactor([secret]).text(f"кнопка[{secret.casefold()}]")
    assert secret.casefold() not in cleaned


def test_short_values_are_not_redacted_into_uselessness():
    """Обратная опасность: вычеркнуть слишком много и ослепить владельца."""
    redactor = Redactor(["код"])
    assert redactor.count == 0, "трёхсимвольная строка не должна попадать в редакцию"
    assert redactor.text("код подтверждения") == "код подтверждения"


def test_secret_field_names_are_masked_even_when_value_is_unknown():
    """Третья линия: поле с «секретным» именем не выводится, чем бы оно ни было."""
    out = redact_secrets({"cookie": "sessionid=abc", "set-cookie": "x=y",
                          "recovery_code": "1111-2222", "caption": "тыква"})
    assert out["cookie"] == MASK
    assert out["set-cookie"] == MASK
    assert out["recovery_code"] == MASK
    assert out["caption"] == "тыква"


def test_secret_names_are_masked_inside_lists():
    """Структура наружу редко бывает плоской."""
    out = redact_secrets({"fields": [{"name": "x", "token": "abcdefgh"}]})
    assert out["fields"][0]["token"] == MASK
