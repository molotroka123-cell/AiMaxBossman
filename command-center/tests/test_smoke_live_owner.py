"""Живой прогон глазами владельца: включить приложение кнопкой из дашборда.

Проверяется ровно то, на что владелец пожаловался вслух: «все приложения
выключены, не могу включить их через дашборд». Поэтому здесь ничего не
подменяется — настоящее приложение из apps/, настоящий браузер, настоящая
кнопка. Остальные наборы работают на приложениях-заглушках и такой ответ
дать не могут: заглушка стартует всегда, а вопрос был про эти восемь.

Пропуск честный и узкий: нет Chromium, нет этого приложения среди
манифестов или его порт занят кем-то посторонним. Всё прочее — падение.
"""
from __future__ import annotations

import pytest

from bcc.features import apps_control as ac
from bcc.features import command_bar as cb

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(300),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

APP = "file-commander-mini"


def _app_missing() -> bool:
    return APP not in ac.known_app_dirs()


def _port_taken() -> bool:
    """Порт занят чужим процессом: прогон не про это, и чужое мы не трогаем."""
    return ac.port_busy(8911)


@pytest.fixture(autouse=True)
def _flags_on(monkeypatch):
    monkeypatch.setenv(ac.FLAG, "1")
    monkeypatch.setenv(cb.FLAG, "1")


@pytest.mark.skipif(_app_missing(), reason=f"приложения {APP} нет среди манифестов")
@pytest.mark.skipif(_port_taken(), reason="порт приложения занят посторонним процессом")
def test_owner_starts_a_real_app_from_the_dashboard(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)

        page.goto(live.url + "/#/apps", wait_until="domcontentloaded")
        page.wait_for_selector("#view", timeout=15000)
        page.wait_for_timeout(3000)

        state = page.evaluate(
            "async (id) => (await (await fetch(`/api/apps/${id}/process`)).json())", APP)
        assert state["enabled"] is True, state
        assert state["running"] is False, "приложение уже запущено — прогон не про это"

        started = page.evaluate(
            "async (id) => { const r = await fetch(`/api/apps/${id}/start`, {method:'POST',"
            " headers:{'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''}});"
            " return {status: r.status, body: await r.json()}; }", APP)
        assert started["status"] == 200, started
        body = started["body"]
        assert body["ok"] is True and body["ready"] is True, body

        after = page.evaluate(
            "async (id) => (await (await fetch(`/api/apps/${id}/process`)).json())", APP)
        assert after["running"] is True and after["owned"] is True and after["pid"], after

        stopped = page.evaluate(
            "async (id) => { const r = await fetch(`/api/apps/${id}/stop`, {method:'POST',"
            " headers:{'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''}});"
            " return {status: r.status, body: await r.json()}; }", APP)
        assert stopped["status"] == 200 and stopped["body"]["stopped"] is True, stopped

        free = page.evaluate(
            "async (id) => (await (await fetch(`/api/apps/${id}/process`)).json())", APP)
        assert free["running"] is False and free["port_busy"] is False, free

    assert not errors, f"ошибки в консоли браузера: {errors}"


def test_owner_sees_the_start_button_and_the_new_pages(live):  # noqa: F811
    """Кнопка, командная строка и операторский канал видны на живом экране."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)

        page.wait_for_selector("#cmdbar-input", timeout=15000)      # командная строка смонтирована
        page.goto(live.url + "/#/apps", wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        html = page.inner_html("#view")
        assert "Запустить" in html, "кнопки запуска нет на странице приложений"

        page.goto(live.url + "/#/mission_console", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        console_html = page.inner_html("#view")
        assert console_html.strip(), "операторский канал не отрисовался"
        assert "\ufffd" not in console_html, "кракозябры на экране"
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert overflow is False, "горизонтальная прокрутка на широком экране"

    assert not errors, f"ошибки в консоли браузера: {errors}"
