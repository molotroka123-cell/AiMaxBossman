"""Один честный ответ на вопрос «есть ли браузер», для всех тестов сразу.

Раньше три файла проверяли ЖЁСТКИЙ путь `/opt/pw-browsers/chromium` — это
раскладка нашего контейнера разработки. На раннере GitHub Actions Playwright
кладёт браузер в `~/.cache/ms-playwright`, поэтому браузерные тесты там
пропускались, ХОТЯ Chromium был установлен: CI был зелёным по неполному набору,
и заметить это по строке «N passed» было нельзя.

Используем тот же поиск исполняемого файла, что и рантайм. Discovery не запускает
sync_playwright: импорт pytest-модуля может происходить внутри asyncio loop, а
поднятый ради проверки драйвер оставлял незавершённую Connection.init.
"""
from __future__ import annotations

import os
from pathlib import Path

from bcc.browser_runtime import chromium_executable

PREINSTALLED = Path("/opt/pw-browsers/chromium")
# CI ставит Playwright и Chromium намеренно и обязана их ПРОГНАТЬ. Без этого
# флага «браузера нет — пропустили» и «браузер есть — прошли» выглядят в
# отчёте одинаково, и потерянное покрытие заметить нельзя.
REQUIRE_ENV = "BCC_REQUIRE_BROWSER"


def required() -> bool:
    return os.environ.get(REQUIRE_ENV, "").strip().lower() in ("1", "true", "yes")


def chromium_path() -> str | None:
    return chromium_executable(preinstalled=str(PREINSTALLED))


def chromium_available() -> bool:
    return chromium_path() is not None


def reason() -> str:
    return "Chromium недоступен: ни /opt/pw-browsers/chromium, ни путь от Playwright"


def click_in_preview(page, selector: str, *, frame_selector: str = "iframe.bd-frame") -> None:
    """Click an element inside the Web Designer preview iframe.

    `frame_locator(...).click()` does not work here, and the reason is the
    preview's fit-to-panel zoom rather than anything broken in the product: the
    iframe carries a CSS `transform: scale(...)`, and Playwright maps
    frame-local coordinates to page coordinates without it, so the synthetic
    click lands outside the element — verified directly, `window.__clicks`
    inside the frame stays 0 while a raw `page.mouse.click` at the same spot
    does reach the picker and produce a `select` message.

    Mapping the coordinates by hand does not fix it either: at a 0.28x zoom a
    heading is a few physical pixels tall, so the rounded centre lands in its
    parent and the wrong element gets selected.

    So the click is dispatched on the element itself, inside the frame. The
    picker listens with `document.addEventListener('click', ..., true)`, which
    is exactly what a real user's click reaches, so this exercises the picker's
    real contract — "clicking this element selects it" — without depending on
    pixel arithmetic through a zoom that the product legitimately applies.
    """
    element = page.frame_locator(frame_selector).locator(selector).first
    element.wait_for()
    element.evaluate("el => el.click()")


__all__ = ["chromium_available", "chromium_path", "click_in_preview", "reason", "required",
           "REQUIRE_ENV", "PREINSTALLED"]
