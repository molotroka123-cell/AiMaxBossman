"""Настоящий Chromium на локальной фикстуре. Instagram здесь нет.

Эти тесты существуют ради одного вопроса: говорят ли тесты на `FixtureDom`
хоть что-нибудь о настоящем браузере. Модуль `dom.py` обещает, что у порта две
реализации с ОДИНАКОВОЙ семантикой; пока это не сверено на одной и той же
странице, тесты на фикстуре доказывают только сами себя.

Что здесь проверяется:

* разбор страницы совпадает у Python-половины и у JavaScript-половины;
* признак секретности совпадает, и значение секретного поля не покидает
  настоящую страницу — не только выдуманную;
* постоянные контексты двух аккаунтов физически не видят данных друг друга.

Чего здесь НЕТ и быть не может: ни одного обращения к Instagram, ни одной
возможности, помеченной по итогам этих тестов как подтверждённая. Живого
аккаунта в этой среде нет; максимум, что даёт этот файл, — `MOCK PASS` против
локальной фикстуры (`PRE_IMPLEMENTATION_AUDIT`, B3).
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from browser_kit import (CSRF_TOKEN, FEED_HTML, LOGIN_HTML, OTP_CODE, PASSWORD,
                         feed_elements, feed_page, login_elements, login_page)
from social_farm.browser import (FixtureDom, FixturePage, PlaywrightDom,
                                 playwright_available)

pytestmark = pytest.mark.real_browser


def chromium_path() -> str:
    """Готовый Chromium, если он есть. `playwright install` не запускается."""
    explicit = os.environ.get("SF_BROWSER_CHROMIUM_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    "/opt/pw-browsers/chromium-*/chrome-linux/headless_shell",
                    str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome")):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return ""


CHROMIUM = chromium_path()
needs_browser = pytest.mark.skipif(
    not (playwright_available() and CHROMIUM),
    reason=("NOT RUN: настоящий браузер недоступен (нет Playwright или Chromium). "
            "Проверено только на фикстуре — это блокер среды, а не отказ кода."))


class RealPage:
    """Одна страница настоящего Chromium на время теста."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self.dom: PlaywrightDom | None = None

    async def open(self, markup: str) -> PlaywrightDom:
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            executable_path=CHROMIUM, headless=True)
        page = await self._browser.new_page()
        await page.set_content(markup)
        self.dom = PlaywrightDom(page=page)
        return self.dom

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


@pytest.fixture()
async def real_page():
    page = RealPage()
    try:
        yield page
    finally:
        await page.close()


def comparable(items: list[dict]) -> list[dict]:
    """Всё, кроме ссылки: `ref` привязан к поколению и не обязан совпадать."""
    return [{k: v for k, v in item.items() if k != "ref"} for item in items]


# ------------------------------------------------------------------ семантика разбора

@needs_browser
@pytest.mark.parametrize("markup,fixture_elements", [
    (LOGIN_HTML, login_elements), (FEED_HTML, feed_elements)])
async def test_both_halves_of_the_port_read_the_same_page_the_same_way(
        real_page, markup, fixture_elements):
    """Разбор на JavaScript и разбор на Python обязаны совпасть дословно.

    Если этот тест упал, все остальные тесты на фикстуре перестали что-либо
    говорить о настоящем браузере: они проверяют другой код.
    """
    real = await real_page.open(markup)
    fixture = FixtureDom(FixturePage(elements=fixture_elements()))

    from_browser = comparable(await real.elements(200))
    from_fixture = comparable(await fixture.elements(200))
    assert from_browser == from_fixture


@needs_browser
@pytest.mark.parametrize("kind,value", [
    ("role", "button|Войти"),
    ("label", "Пароль"),
    ("accessible_name", "Имя пользователя"),
    ("stable_attribute", "id=legacy"),
    ("css", "#otp"),
    ("xpath", "//input[@id='csrf']"),
])
async def test_search_strategies_find_the_same_thing_in_both_halves(
        real_page, kind, value):
    real = await real_page.open(LOGIN_HTML)
    fixture = FixtureDom(login_page())
    assert comparable(await real.find(kind, value)) == \
        comparable(await fixture.find(kind, value))


# ------------------------------------------------------------------ секреты

@needs_browser
async def test_no_secret_value_leaves_a_real_page(real_page):
    """То же доказательство, что и на фикстуре, но на настоящем DOM."""
    real = await real_page.open(LOGIN_HTML)
    elements = await real.elements(200)
    dumped = repr(elements)
    for secret in (PASSWORD, CSRF_TOKEN, OTP_CODE):
        assert secret not in dumped
    secret_fields = [e for e in elements if e["secret"]]
    assert len(secret_fields) == 4, "секретными признаны не все опасные поля"
    assert all(e["filled"] for e in secret_fields)
    assert all(e["text"] == "" for e in secret_fields)


@needs_browser
async def test_an_ordinary_field_still_shows_its_value(real_page):
    """Обратная проверка: прятать всё подряд — значит ослепить владельца."""
    real = await real_page.open(
        '<label for="caption">Подпись</label>'
        '<textarea id="caption">осенняя витрина</textarea>')
    found = await real.find("label", "Подпись")
    assert found and found[0]["text"] == "осенняя витрина"


@needs_browser
async def test_a_secret_typed_by_the_runtime_does_not_come_back(real_page):
    """Пишем пароль в настоящее поле и убеждаемся, что прочитать его нельзя."""
    real = await real_page.open(LOGIN_HTML)
    found = await real.find("label", "Пароль")
    await real.fill(found[0]["ref"], "novyj-parol-2026")
    again = await real.elements(200)
    assert "novyj-parol-2026" not in repr(again)
    assert [e for e in again if e["type"] == "password"][0]["filled"] is True


# ------------------------------------------------------------------ изоляция

@needs_browser
async def test_persistent_contexts_of_two_accounts_do_not_see_each_other(tmp_path):
    """Проверяется не «каталоги разные», а что данные одного не видны другому.

    Cookie аккаунта A кладётся в его контекст; контекст аккаунта B открывается
    из своего каталога и той же cookie не видит. Это и есть «no context reuse
    across accounts» из спецификации, проверенное настоящим браузером.
    """
    import stat

    from social_farm.browser.config import BrowserConfig
    from social_farm.browser.runtime import PlaywrightRuntime

    runtime = PlaywrightRuntime(config=BrowserConfig(
        context_root=tmp_path, headless=True, chromium_executable=CHROMIUM))
    try:
        await runtime.open("acc-A")
        await runtime.open("acc-B")
        context_a = runtime._contexts["acc-A"]
        context_b = runtime._contexts["acc-B"]
        assert context_a is not context_b

        await context_a.add_cookies([{
            "name": "sessionid", "value": "cookie-akkaunta-A",
            "url": "https://fixture.local/"}])
        seen_by_a = await context_a.cookies("https://fixture.local/")
        seen_by_b = await context_b.cookies("https://fixture.local/")
        assert [c["value"] for c in seen_by_a] == ["cookie-akkaunta-A"]
        assert seen_by_b == [], "контекст аккаунта B видит сессию аккаунта A"

        for account in ("acc-A", "acc-B"):
            directory = runtime.context_root.path_for(account)
            assert directory.exists()
            # Загрузки уходят в песочницу внутри каталога аккаунта, а не в
            # общий каталог загрузок машины (`32_BROWSER_SECURITY`).
            assert (directory / "downloads").is_dir()
            if os.name == "posix":
                assert stat.S_IMODE(directory.stat().st_mode) == 0o700
            runtime.context_root.assert_owned(account, directory)
    finally:
        await runtime.close()


@needs_browser
async def test_a_directory_belonging_to_another_account_is_never_opened(tmp_path):
    """Подменённый каталог отвергается ДО запуска браузера."""
    from social_farm.browser import CrossAccountViolation
    from social_farm.browser.config import BrowserConfig
    from social_farm.browser.runtime import PlaywrightRuntime

    runtime = PlaywrightRuntime(config=BrowserConfig(
        context_root=tmp_path, headless=True, chromium_executable=CHROMIUM))
    try:
        directory = runtime.context_root.prepare("acc-A")
        (directory / ".account").write_text("acc-B", encoding="utf-8")
        with pytest.raises(CrossAccountViolation):
            await runtime.open("acc-A")
        assert not runtime._contexts
    finally:
        await runtime.close()
