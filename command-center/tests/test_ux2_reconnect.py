"""UX 2.0 — перезапуск/переподключение: реальный Chromium против реального сервера.
Сервер останавливается и поднимается заново на том же порту; владелец должен
видеть обратный отсчёт до повтора, баннер «данные могли устареть» с кнопкой
«Переподключить сейчас», а после восстановления — тост «Соединение
восстановлено / данные обновлены» и исчезновение баннера."""
from __future__ import annotations

import re

import pytest

from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401  (фикстура live)
from .browser_support import chromium_available, reason as browser_reason

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

NETWORK_NOISE = re.compile(r"WebSocket|net::ERR_|Failed to load resource|ERR_CONNECTION", re.I)


def test_restart_shows_countdown_banner_and_restored_toast(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not NETWORK_NOISE.search(m.text) else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)

        # 1. на связи: live-обновления, баннера нет
        page.wait_for_function("document.getElementById('conn-text').textContent === 'live-обновления'", timeout=15000)
        assert page.locator("#stale-banner").is_hidden()

        # 2. сервер упал: обратный отсчёт до повтора + баннер устаревших данных
        live.stop()
        page.wait_for_selector("#stale-banner:not([hidden])", timeout=15000)
        page.wait_for_function(
            "/нет соединения · повтор через \\d+ с/.test(document.getElementById('conn-text').textContent)", timeout=20000)
        banner = page.locator("#stale-text").inner_text()
        assert "Нет связи с сервером с" in banner and "могли устареть" in banner
        assert re.search(r"Повтор через \d+ с|Подключаемся…|Повтор…", page.locator("#stale-retry").inner_text())
        assert page.locator("#conn").get_attribute("data-state") in ("closed", "connecting")

        # 3. «Переподключить сейчас» при лежащем сервере — честно возвращаемся к отсчёту, без ошибок в консоли
        attempt_before = page.evaluate("window.__bxConn.bus.attempt")
        assert attempt_before >= 1
        page.click("#stale-now")
        page.wait_for_function(
            "/нет соединения · повтор через \\d+ с/.test(document.getElementById('conn-text').textContent)", timeout=20000)
        assert page.locator("#stale-banner").is_visible()

        # 4. сервер вернулся: тост о восстановлении, данные обновлены, баннер спрятан
        live.restart()
        page.click("#stale-now")  # владелец не обязан ждать backoff
        page.wait_for_selector(".toast:has-text('Соединение восстановлено')", timeout=30000)
        toast = page.locator(".toast:has-text('Соединение восстановлено')").first.inner_text()
        assert "Данные обновлены" in toast and "без связи" in toast
        page.wait_for_function("document.getElementById('conn-text').textContent === 'live-обновления'", timeout=15000)
        page.wait_for_selector("#stale-banner[hidden]", state="attached", timeout=15000)
        assert page.locator("#conn").get_attribute("data-state") == "open"
        assert page.evaluate("window.__bxConn.bus.disconnectedAt") == 0

        # 5. поток событий снова живой после перезапуска
        live.emit("task.started", run_id=777, task_id="t1", title="после перезапуска", model="qwen-14b")
        page.wait_for_function(
            "window.__bxThinking && window.__bxThinking.runs.has(777)",
            timeout=15000)
        browser.close()

    assert errors == [], errors
