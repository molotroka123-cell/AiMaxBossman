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
