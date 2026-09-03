"""BOSSMAN Command Center как настольное приложение (UX 2.0, UX2-4).

Без нового GUI-фреймворка: окно — это установленный Chromium/Chrome/Edge в режиме
``--app=URL`` (без адресной строки и вкладок, свой профиль в data_dir), сервер —
тот же ``bcc`` (uvicorn) в фоновом потоке. Если сервер уже запущен на этом порту,
второй не поднимается — окно просто подключается к работающему.

Секрет доступа (токен) в URL НЕ передаётся: вход остаётся через форму, cookie
сессии живёт в отдельном профиле окна и переживает перезапуски.

Запуск: ``bcc-desktop`` (console script) или ``python -m bcc.desktop``.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

# Кандидаты в порядке предпочтения: предустановленный Playwright-Chromium (его же
# использует рантайм браузера), затем системные браузеры на Chromium-движке.
BROWSER_CANDIDATES: tuple[str, ...] = (
    os.environ.get("BCC_DESKTOP_BROWSER", ""),
    "/opt/pw-browsers/chromium",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "microsoft-edge",
    "msedge",
    "brave-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser(candidates: Sequence[str] = BROWSER_CANDIDATES) -> str | None:
    """Первый существующий Chromium-совместимый браузер (абсолютный путь) или None."""
    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if p.is_absolute():
            if p.exists():
                return str(p)
            continue
        found = shutil.which(cand)
        if found:
            return found
    return None


def browser_argv(browser: str, url: str, profile_dir: Path, *, window_size: str = "1440,900",
                 extra: Sequence[str] = ()) -> list[str]:
    """argv окна-приложения. Только флаги, без shell; токена/секретов в URL нет."""
    if "token=" in url:
        raise ValueError("секрет в URL окна недопустим — вход через форму, cookie в профиле")
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={window_size}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--log-level=3",
        *extra,
    ]


def server_alive(url: str, timeout: float = 1.5) -> bool:
    """Отвечает ли уже кто-то по этому адресу (любой HTTP-ответ, кроме 5xx)."""
    # Локальный адрес: системный/корпоративный прокси из окружения обходим явно,
    # иначе проверка «сервер уже работает» уходит на прокси и врёт.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:  # noqa: S310 — локальный адрес владельца
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def open_window(browser: str, url: str, profile_dir: Path, *, extra: Sequence[str] = (),
                window_size: str = "1440,900") -> subprocess.Popen:
    """Запускает процесс окна и сразу возвращает его (для тестов и внешнего управления)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    argv = browser_argv(browser, url, profile_dir, window_size=window_size, extra=extra)
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL)  # noqa: S603 — argv-only, без shell


def launch_window(browser: str, url: str, profile_dir: Path, *, extra: Sequence[str] = (),
                  window_size: str = "1440,900", timeout: float | None = None) -> int:
    """Открывает окно и ждёт его закрытия; возвращает код выхода браузера."""
    proc = open_window(browser, url, profile_dir, extra=extra, window_size=window_size)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 124


class _BackgroundServer:
    """uvicorn в потоке — ровно тот же app, что у ``bcc``; останавливается вместе с окном."""

    def __init__(self, host: str, port: int) -> None:
        import uvicorn

        from .app import create
        from .config import settings

        settings.ensure_dirs()
        self.server = uvicorn.Server(uvicorn.Config(create(), host=host, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, name="bcc-desktop-server", daemon=True)

    def start(self, url: str, timeout: float = 30.0) -> bool:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if server_alive(url):
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.2)
        return False

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bcc-desktop", description="BOSSMAN Command Center — настольное окно")
    p.add_argument("--host", default=None, help="адрес сервера (по умолчанию BCC_HOST или 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="порт сервера (по умолчанию BCC_PORT или 8800)")
    p.add_argument("--browser", default=None, help="путь к Chromium/Chrome/Edge (иначе автопоиск)")
    p.add_argument("--profile", default=None, help="каталог профиля окна (по умолчанию <data_dir>/desktop-profile)")
    p.add_argument("--window-size", default="1440,900")
    p.add_argument("--no-server", action="store_true", help="не поднимать сервер, только окно")
    p.add_argument("--browser-arg", action="append", default=[], help="дополнительный флаг браузеру (можно несколько)")
    return p


def run(argv: Sequence[str] | None = None, *, launcher: Callable[..., int] = launch_window,
        out=sys.stdout) -> int:
    """Точка входа с инъекцией launcher'а для тестов. Коды: 0 ок, 2 нет браузера, 3 сервер не поднялся."""
    from .config import settings

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}/"
    profile_dir = Path(args.profile) if args.profile else Path(settings.data_dir) / "desktop-profile"

    browser = args.browser or find_browser()
    if not browser:
        print("[bcc-desktop] не найден Chromium/Chrome/Edge — укажите --browser или BCC_DESKTOP_BROWSER;"
              " веб-версия остаётся доступной командой `bcc`.", file=out, flush=True)
        return 2

    started: _BackgroundServer | None = None
    if server_alive(url):
        print(f"[bcc-desktop] сервер уже работает: {url} — подключаюсь к нему", file=out, flush=True)
    elif args.no_server:
        print(f"[bcc-desktop] сервер по адресу {url} не отвечает, а --no-server задан", file=out, flush=True)
        return 3
    else:
        started = _BackgroundServer(host, port)
        if not started.start(url):
            print(f"[bcc-desktop] сервер не поднялся на {url}", file=out, flush=True)
            started.stop()
            return 3
        print(f"[bcc-desktop] сервер запущен: {url}", file=out, flush=True)

    print(f"[bcc-desktop] окно: {browser} (профиль {profile_dir})", file=out, flush=True)
    try:
        code = launcher(browser, url, profile_dir, extra=tuple(args.browser_arg), window_size=args.window_size)
    finally:
        if started is not None:
            started.stop()
    return int(code or 0)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
