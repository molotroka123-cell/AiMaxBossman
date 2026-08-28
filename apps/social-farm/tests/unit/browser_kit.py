"""Общая фикстура браузерных тестов: одна страница входа для всех проверок.

Страница описана ДВАЖДЫ — разметкой и элементами фикстуры, — и это сделано
намеренно. Настоящий Chromium разбирает разметку, `FixtureDom` разбирает
элементы, и `test_browser_dom_parity.py` сверяет, что оба разбора дают один и
тот же ответ. Пока это совпадение доказано, тест на фикстуре что-то говорит и о
настоящем браузере; без него он говорил бы только о самом себе.

Настоящего Instagram здесь нет и быть не может: живого аккаунта в этой среде
нет, а заводить его ради теста запрещено границей приложения. Всё, что
доказывают эти тесты, — это поведение защит, а не работоспособность
возможностей Instagram.
"""
from __future__ import annotations

import json
from typing import Any

from social_farm.browser import (AccountBrowserSession, BrowserConfig, FixtureDom,
                                 FixtureElement, FixturePage, MappingSecretResolver,
                                 SelectorRegistry)

# Значения, которые не должны появиться НИГДЕ на выходе. Длинные и заметные:
# короткую строку можно случайно найти в чужом тексте, и тест соврал бы в обе
# стороны.
PASSWORD = "ochen-sekretnyj-parol-2026"
CSRF_TOKEN = "csrf-9f2a1c4e8b7d6a5f0e3c2b1a"
OTP_CODE = "918273"
SESSION_COOKIE = "sessionid-77aa11bb22cc33dd44ee"

LOGIN_URL = "https://fixture.local/accounts/login/"

# Разметка страницы входа. Ровно четыре способа спрятать секрет в поле ввода —
# те же четыре, что однажды нашлись рабочими в браузере плоскости управления.
LOGIN_HTML = f"""\
<!doctype html>
<html lang="ru">
<head><title>Вход — фикстура</title></head>
<body>
<h1>Вход в аккаунт</h1>
<form method="GET" action="/accounts/login/">
  <label for="username">Имя пользователя</label>
  <input id="username" name="username" type="text" value="">
  <label for="password">Пароль</label>
  <input id="password" name="password" type="password" value="{PASSWORD}">
  <input id="csrf" name="csrf_token" type="hidden" value="{CSRF_TOKEN}">
  <input id="legacy" name="legacy_password" type="text"
         autocomplete="current-password" value="{PASSWORD}">
  <input id="otp" name="security_code" type="text"
         autocomplete="one-time-code" value="{OTP_CODE}">
  <button type="submit">Войти</button>
</form>
</body>
</html>
"""

LOGIN_TEXT = "Вход в аккаунт Имя пользователя Пароль Войти"


def login_elements() -> list[FixtureElement]:
    """Те же элементы, что видит настоящий браузер на `LOGIN_HTML`."""
    return [
        FixtureElement(tag="h1", text="Вход в аккаунт"),
        # Элементы `label` браузер тоже отдаёт кандидатами — они здесь не для
        # красоты: без них сверка с настоящим Chromium была бы неполной.
        FixtureElement(tag="label", text="Имя пользователя"),
        FixtureElement(tag="input", type="text", label="Имя пользователя", value="",
                       attributes={"id": "username", "name": "username"}),
        FixtureElement(tag="label", text="Пароль"),
        FixtureElement(tag="input", type="password", label="Пароль", value=PASSWORD,
                       attributes={"id": "password", "name": "password"}),
        FixtureElement(tag="input", type="hidden", value=CSRF_TOKEN,
                       attributes={"id": "csrf", "name": "csrf_token"}),
        FixtureElement(tag="input", type="text", value=PASSWORD,
                       attributes={"id": "legacy", "name": "legacy_password",
                                   "autocomplete": "current-password"}),
        FixtureElement(tag="input", type="text", value=OTP_CODE,
                       attributes={"id": "otp", "name": "security_code",
                                   "autocomplete": "one-time-code"}),
        FixtureElement(tag="button", text="Войти", type="submit",
                       attributes={"id": "submit"}),
    ]


def login_page() -> FixturePage:
    return FixturePage(url=LOGIN_URL, title="Вход — фикстура", text=LOGIN_TEXT,
                       markup=LOGIN_HTML, elements=login_elements())


# --------------------------------------------------------------- страница ленты

FEED_URL = "https://fixture.local/nashe_ateljie/"
IDENTITY = "nashe_ateljie"

FEED_HTML = """\
<!doctype html>
<html lang="ru">
<head><title>Лента — фикстура</title></head>
<body>
<h1>Публикации</h1>
<div data-testid="viewer">nashe_ateljie</div>
<label for="caption">Подпись</label>
<textarea id="caption" name="caption"></textarea>
<button id="share">Поделиться</button>
<button id="drop">Удалить</button>
</body>
</html>
"""

FEED_TEXT = "Публикации nashe_ateljie Подпись Поделиться Удалить"


def feed_elements(*, identity: str = IDENTITY) -> list[FixtureElement]:
    return [
        FixtureElement(tag="h1", text="Публикации"),
        FixtureElement(tag="div", text=identity,
                       attributes={"data-testid": "viewer"}),
        FixtureElement(tag="label", text="Подпись"),
        FixtureElement(tag="textarea", label="Подпись", value="",
                       attributes={"id": "caption", "name": "caption"}),
        FixtureElement(tag="button", text="Поделиться", attributes={"id": "share"}),
        FixtureElement(tag="button", text="Удалить", attributes={"id": "drop"}),
    ]


def feed_page(*, identity: str = IDENTITY) -> FixturePage:
    return FixturePage(url=FEED_URL, title="Лента — фикстура",
                       text=FEED_TEXT.replace(IDENTITY, identity),
                       markup=FEED_HTML.replace(IDENTITY, identity),
                       elements=feed_elements(identity=identity))


# --------------------------------------------------------------- пакет селекторов

def pack_document() -> dict[str, Any]:
    """Пакет фикстуры. Классы безопасности берутся из доменного каталога через
    поле `capability` — пакет данных не вправе назначать их сам."""
    return {
        "provider": "fixture",
        "version": "1.0.0",
        "ui_revision": "2026-08-28",
        "locale": "ru",
        "actions": [
            {"action": "account.identity.read", "target": "имя владельца сессии",
             "capability": "account.read",
             "strategies": [{"kind": "stable_attribute", "value": "data-testid=viewer"}]},
            {"action": "login.username.fill", "target": "поле имени пользователя",
             "capability": "account.read",
             "strategies": [{"kind": "label", "value": "Имя пользователя"}],
             "confirmation_text": "вход в аккаунт"},
            {"action": "login.password.fill", "target": "поле пароля",
             "capability": "account.read",
             "strategies": [{"kind": "label", "value": "Пароль"}],
             "confirmation_text": "вход в аккаунт"},
            {"action": "login.legacy_password.fill", "target": "текстовое поле пароля",
             "capability": "account.read",
             "strategies": [{"kind": "stable_attribute", "value": "id=legacy"}],
             "confirmation_text": "вход в аккаунт"},
            {"action": "login.submit", "target": "кнопка «Войти»",
             "capability": "account.read",
             "strategies": [{"kind": "role", "value": "button|Войти"}]},
            {"action": "content.draft", "target": "поле подписи",
             "capability": "content.draft",
             "strategies": [{"kind": "label", "value": "Подпись"}]},
            {"action": "media.publish.image", "target": "кнопка «Поделиться»",
             "capability": "media.publish.image",
             "strategies": [{"kind": "role", "value": "button|Поделиться"}],
             "postconditions": ["text_contains:опубликовано"]},
            {"action": "media.delete", "target": "кнопка «Удалить»",
             "capability": "media.delete",
             "strategies": [{"kind": "role", "value": "button|Удалить"}],
             "confirmation_text": "удалить публикацию?",
             "postconditions": ["text_absent:удалить публикацию?"]},
        ],
    }


def registry() -> SelectorRegistry:
    reg = SelectorRegistry()
    reg.register_document(pack_document())
    return reg


def session(dom: FixtureDom, *, account_id: str = "acc-A",
            identity: str = IDENTITY, secrets: dict[str, str] | None = None,
            config: BrowserConfig | None = None, landing_url: str = "",
            **kwargs: Any) -> AccountBrowserSession:
    return AccountBrowserSession(
        account_id=account_id, expected_identity=identity, dom=dom,
        registry=registry(), provider="fixture", config=config or BrowserConfig(),
        resolver=MappingSecretResolver(dict(secrets or {})),
        landing_url=landing_url, **kwargs)


def login_session(**kwargs: Any) -> tuple[FixtureDom, AccountBrowserSession]:
    """Сессия на странице входа: личности на ней нет, значит нужен человек."""
    dom = FixtureDom(login_page())
    return dom, session(dom, **kwargs)


def ready_session(*, identity: str = IDENTITY,
                  **kwargs: Any) -> tuple[FixtureDom, AccountBrowserSession]:
    """Сессия на ленте: личность видна, значит сессия дойдёт до READY."""
    dom = FixtureDom(feed_page(identity=identity))
    return dom, session(dom, **kwargs)


def flatten(value: Any) -> str:
    """Всё, что уходит наружу, одной строкой — чтобы искать в ней утечку.

    Именно так секрет и утекает на самом деле: не «в поле X», а куда-нибудь в
    сериализацию целиком. Поэтому и проверять надо целиком.
    """
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["CSRF_TOKEN", "FEED_HTML", "FEED_TEXT", "FEED_URL", "IDENTITY",
           "LOGIN_HTML", "LOGIN_TEXT", "LOGIN_URL", "OTP_CODE", "PASSWORD",
           "SESSION_COOKIE", "feed_elements", "feed_page", "flatten",
           "login_elements", "login_page", "login_session", "pack_document",
           "ready_session", "registry", "session"]
