"""Один честный ответ на вопрос «есть ли браузер» — для всех тестов ядра сразу.

Тесты раньше хардкодили linux-путь (`/usr/bin/chromium`,
`/opt/pw-browsers/chromium-1194/...`). На Windows такого пути нет, а
`chromium.launch(executable_path=<несуществующий>)` не падает быстро, а ВИСНЕТ —
из-за чего полный прогон `pytest tests` не завершался вовсе.

Ищем бинарь по стандартным раскладкам Playwright для всех ОС — без запуска
драйвера, чтобы поиск не имел побочных эффектов (запуск sync_playwright внутри
процесса с asyncio-тестами оставляет висящие задачи и сам по себе рискован на
Windows). sync_playwright остаётся крайним случаем для нестандартной раскладки.

Нет браузера — честный skip, а не зависание и не FAIL. Переменная
BOSSMAN_TEST_CHROMIUM приоритетнее всего, но только если путь существует.
"""
from __future__ import annotations

import glob
import os
from functools import lru_cache
from pathlib import Path


def _browser_roots() -> list[str]:
    """Каталоги, куда Playwright кладёт браузеры, по всем ОС."""
    roots: list[str] = []
    env_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env_root and env_root != "0":
        roots.append(env_root)
    home = Path.home()
    roots += [
        "/opt/pw-browsers",                                  # контейнер разработки
        str(home / ".cache" / "ms-playwright"),               # linux
        str(home / "AppData" / "Local" / "ms-playwright"),    # windows
        str(home / "Library" / "Caches" / "ms-playwright"),   # macOS
    ]
    return roots


# Относительные пути к исполняемому файлу внутри каталога chromium-*.
_EXE_RELATIVE = (
    "chrome-linux/chrome",
    "chrome-win/chrome.exe",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
)

# Системные установки как последний штрих (linux-дистрибутивы).
_SYSTEM_PATHS = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome")


@lru_cache(maxsize=1)
def chromium_path() -> str | None:
    """Путь к Chromium или None. Порядок: переменная → раскладки Playwright →
    системные пути → (крайний случай) запрос у самого Playwright."""
    env = os.getenv("BOSSMAN_TEST_CHROMIUM")
    if env and Path(env).exists():
        return env

    for root in _browser_roots():
        for rel in _EXE_RELATIVE:
            # chromium-1194, chromium_headless_shell-* и т.п.; берём свежую версию
            for hit in sorted(glob.glob(os.path.join(root, "chromium-*", *rel.split("/"))), reverse=True):
                if Path(hit).exists():
                    return hit
            direct = os.path.join(root, "chromium", *rel.split("/"))
            if Path(direct).exists():
                return direct

    for sys_path in _SYSTEM_PATHS:
        if Path(sys_path).exists():
            return sys_path

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception:
        return None
    return path if path and Path(path).exists() else None


def chromium_available() -> bool:
    return chromium_path() is not None


def reason() -> str:
    return ("Chromium недоступен: ни BOSSMAN_TEST_CHROMIUM, ни раскладки "
            "Playwright/ms-playwright, ни системные пути")


__all__ = ["chromium_path", "chromium_available", "reason"]
