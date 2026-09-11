"""BL-027: панель приложений перекрывала последний орган управления страницы.

Найдено обходом раздела 23 (`scripts/ui_acceptance_sweep.py`): на странице
«Пульт» нажатие на START не проходило. Диагноз не «страница мигает» и не
«кнопки нет» — журнал вызова Playwright назвал виновника дословно:

    <nav id="desktop-dock" class="desktop-dock">…</nav> intercepts pointer events

Панель приложений — `position: fixed; bottom: 14px; z-index: 45` — висит
поверх содержимого, а у области содержимого в той же медиа-выборке было
`padding: 24px`. Прокрутить ниже панели нельзя: документ кончается. Значит
последний экран любой ДОСТАТОЧНО ДЛИННОЙ страницы уезжает под панель, и
владелец видит кнопку, но нажать её не может.

Проверка идёт настоящим кликом настоящей мыши, а не измерением отступа: отступ
можно поменять и всё равно промахнуться (панель центрирована и её высота
зависит от содержимого). Клик — единственное, что доказывает достижимость.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_the_last_control_of_a_long_page_is_clickable_under_the_dock(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            # Рабочий размер окна владельца, а не «пошире, чтобы влезло»:
            # на 1440x900 панель как раз и перекрывала кнопку.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/command")
            start = page.locator("#view button", has_text="START").first
            start.wait_for(timeout=15000)

            # Страница должна быть ДЛИННЕЕ окна — иначе тест ничего не проверяет
            # и останется зелёным даже после возврата дефекта.
            assert page.evaluate(
                "() => document.documentElement.scrollHeight"
            ) > 900, "страница короче окна: этим тестом перекрытие не поймать"

            start.click(timeout=8000)
        finally:
            browser.close()


def test_the_dock_is_still_on_top_of_ordinary_content(live):
    """Обратный контроль: панель не должна потерять своё место.

    «Починка» вида «убрать панель» или «опустить её под содержимое» прошла бы
    первый тест и сломала бы продукт: панель приложений на то и панель, что
    она поверх.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/command")
            page.wait_for_selector("#view button", timeout=15000)
            state = page.evaluate("""() => {
                const dock = document.querySelector('#desktop-dock');
                if (!dock) return {present: false};
                const s = getComputedStyle(dock);
                const r = dock.getBoundingClientRect();
                return {present: true, position: s.position, z: s.zIndex,
                        visible: r.width > 0 && r.height > 0,
                        withinViewport: r.bottom <= window.innerHeight + 1};
            }""")
            assert state["present"] and state["visible"]
            assert state["position"] == "fixed"
            assert int(state["z"]) >= 40
            assert state["withinViewport"]
        finally:
            browser.close()
