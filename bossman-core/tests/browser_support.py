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
import json
import os
from functools import lru_cache
from pathlib import Path


def _playwright_registry() -> tuple[Path | None, str | None]:
    """Read the installed driver's revision without starting an asyncio driver."""
    try:
        import playwright
        package = Path(playwright.__file__).resolve().parent / "driver" / "package"
        browsers = json.loads((package / "browsers.json").read_text(encoding="utf-8"))["browsers"]
        revision = next(row["revision"] for row in browsers if row["name"] == "chromium")
        if isinstance(revision, str) and revision.isascii() and revision.isdigit():
            return package, revision
    except (ImportError, OSError, ValueError, TypeError, KeyError, StopIteration):
        pass
    return None, None


def _browser_roots() -> list[str]:
    """Каталоги, куда Playwright кладёт браузеры, по всем ОС."""
    roots: list[str] = []
    env_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env_root == "0":
        package, _ = _playwright_registry()
        return [str(package / ".local-browsers")] if package else []
    if env_root and env_root != "0":
        return [env_root]
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
    # Current Chrome for Testing layout, as declared by Playwright's registry.
    "chrome-linux64/chrome",
    "chrome-win64/chrome.exe",
    "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-linux/chrome",
    "chrome-win/chrome.exe",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
)

# Системные установки как последний штрих (linux-дистрибутивы).
_SYSTEM_PATHS = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome")


@lru_cache(maxsize=1)
def chromium_path() -> str | None:
    """Explicit owner path, then the installed driver's exact Chromium revision.

    Legacy/system discovery is retained only when driver metadata is absent.
    A stale cache must not silently substitute another engine for the CI target.
    """
    env = os.getenv("BOSSMAN_TEST_CHROMIUM")
    if env and Path(env).is_file():
        return env

    _, revision = _playwright_registry()
    if revision:
        for root in _browser_roots():
            for rel in _EXE_RELATIVE:
                path = Path(root) / f"chromium-{revision}" / rel
                if path.is_file():
                    return str(path)
        return None

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
