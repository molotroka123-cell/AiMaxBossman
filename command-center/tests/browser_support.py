"""Один честный ответ на вопрос «есть ли браузер», для всех тестов сразу.

Раньше три файла проверяли ЖЁСТКИЙ путь `/opt/pw-browsers/chromium` — это
раскладка нашего контейнера разработки. На раннере GitHub Actions Playwright
кладёт браузер в `~/.cache/ms-playwright`, поэтому браузерные тесты там
пропускались, ХОТЯ Chromium был установлен: CI был зелёным по неполному набору,
и заметить это по строке «N passed» было нельзя.

Спрашиваем то же, что спрашивает рантайм (`bcc/v2/browser_control.py` зовёт
`pw.chromium.launch()` и полагается на разрешение пути самим Playwright), а не
угадываем каталог.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PREINSTALLED = Path("/opt/pw-browsers/chromium")
# CI ставит Playwright и Chromium намеренно и обязана их ПРОГНАТЬ. Без этого
# флага «браузера нет — пропустили» и «браузер есть — прошли» выглядят в
# отчёте одинаково, и потерянное покрытие заметить нельзя.
REQUIRE_ENV = "BCC_REQUIRE_BROWSER"


def required() -> bool:
    return os.environ.get(REQUIRE_ENV, "").strip().lower() in ("1", "true", "yes")


@lru_cache(maxsize=1)
def chromium_available() -> bool:
    if PREINSTALLED.exists():
        return True
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        # Вызывается на импорте модуля теста, когда цикла событий ещё нет.
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception:
        return False
    return bool(path) and Path(path).exists()


def reason() -> str:
    return "Chromium недоступен: ни /opt/pw-browsers/chromium, ни путь от Playwright"


__all__ = ["chromium_available", "reason", "required", "REQUIRE_ENV", "PREINSTALLED"]


# ---------------------------------------------------------------------------
# Клик по элементу ВНУТРИ превью Веб-дизайнера
# ---------------------------------------------------------------------------
# `frame_locator(...).click()` Playwright не годится для этого кадра, и это не
# свойство продукта, а ограничение инструмента. Кадр превью:
#   * загружен по URL и помечен sandbox="allow-scripts" — источник непрозрачный,
#     поэтому Chromium уводит кадр в ОТДЕЛЬНЫЙ процесс (OOPIF);
#   * показывается уменьшенным: масштаб задан CSS-трансформом.
# В такой связке Playwright считает точку клика сам и промахивается: при 0.5 он
# сообщает «section.hero intercepts pointer events» для точки, которая должна
# лежать в h1, а при 0.28 в документ кадра не приходит НИ ОДНОГО события.
# Проверено отдельно, что ни песочница, ни трансформ, ни OOPIF сами по себе
# доставку не ломают: настоящий клик мышью в ту же точку экрана приходит в
# документ кадра с ПРАВИЛЬНЫМИ координатами (клик в 40,40 при 0.5 приходит как
# 79,80). Поэтому здесь считается экранная точка и выполняется настоящий клик —
# это и есть то, что делает владелец, а не обход проверки.
def preview_frame(page):
    """Гостевой frame превью Веб-дизайнера (не FrameLocator, а Frame)."""
    for frame in page.frames:
        if "/preview" in (frame.url or ""):
            return frame
    raise AssertionError(f"кадр превью не найден среди {[f.url for f in page.frames]}")


def click_in_preview(page, selector: str, *, index: int = 0, timeout: float = 15000):
    """Настоящий клик мышью по элементу внутри превью.

    Возвращает описание точки — чтобы упавший тест показывал, куда он попал.
    """
    guest = preview_frame(page)
    guest.wait_for_selector(selector, timeout=timeout)
    for _ in range(4):
        box = guest.evaluate(
            """([selector, index]) => {
              const el = document.querySelectorAll(selector)[index];
              if (!el) return null;
              el.scrollIntoView({block: 'center'});
              const r = el.getBoundingClientRect();
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }""",
            [selector, index])
        assert box, f"в превью нет элемента {selector}[{index}]"
        page.evaluate("() => document.querySelector('iframe.bd-frame').scrollIntoView({block: 'center'})")
        spot = page.evaluate(
            """box => {
              const f = document.querySelector('iframe.bd-frame');
              const r = f.getBoundingClientRect();
              const scale = f.offsetWidth ? r.width / f.offsetWidth : 1;
              return {x: r.x + box.x * scale, y: r.y + box.y * scale,
                      vw: window.innerWidth, vh: window.innerHeight, scale};
            }""", box)
        # Точка обязана лежать в видимой области окна: событие мыши за её
        # границей до кадра не доходит, и тест молча «кликает» в пустоту.
        if 0 <= spot["x"] <= spot["vw"] and 0 <= spot["y"] <= spot["vh"]:
            # Наведение ПЕРЕД нажатием — не косметика. Кадр живёт в отдельном
            # процессе, и первое событие по новой раскладке Chromium разрешает
            # асинхронно: само это событие теряется, следующее приходит уже
            # правильно. Владелец подводит указатель к элементу заранее и этого
            # не замечает; тест, который бьёт мышью без наведения, ловит ровно
            # тот единственный потерянный клик.
            page.mouse.move(spot["x"], spot["y"])
            page.wait_for_timeout(150)
            page.mouse.click(spot["x"], spot["y"])
            return spot
        page.evaluate("spot => window.scrollBy(0, spot.y - spot.vh / 2)", spot)
    raise AssertionError(f"точку элемента {selector}[{index}] не удалось вывести в окно: {spot}")


def wait_for_preview_viewport(page, width: int, height: int | None = None, *, timeout: float = 10000):
    """Дождаться, пока ОКНО КАДРА действительно стало нужного размера.

    Ждать `iframe.style.width` нельзя: это значение хоста, оно меняется
    мгновенно, а кадр живёт в другом процессе и пересчитывает свою раскладку
    позже — замер сразу после установки стиля читает СТАРЫЙ макет (медиазапрос
    ещё не сработал). Разница измерена: сразу после установки 390px окно кадра
    всё ещё 1440×900, через ~250 мс — 390×844.
    """
    guest = preview_frame(page)
    expected = [width] if height is None else [width, height]
    guest.wait_for_function(
        """expected => expected.length === 1
             ? window.innerWidth === expected[0]
             : window.innerWidth === expected[0] && window.innerHeight === expected[1]""",
        arg=expected, timeout=timeout)
    return guest


__all__ += ["preview_frame", "click_in_preview", "wait_for_preview_viewport"]
