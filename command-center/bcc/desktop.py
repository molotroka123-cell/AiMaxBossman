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
import json
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
        "--enable-logging",  # chrome_debug.log остаётся в профиле: тихий краш окна расследуем по нему
        *extra,
    ]


APP_IDENTITY = "bossman-command-center"


def _get_json(url: str, timeout: float) -> dict | None:
    """GET JSON в обход прокси из окружения (адрес локальный, прокси только врёт)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:  # noqa: S310 — локальный адрес владельца
            if resp.status != 200:
                return None
            data = json.loads(resp.read(64_000).decode("utf-8", "replace"))
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def identify_server(base_url: str, timeout: float = 2.0) -> dict | None:
    """Кто слушает порт. Возвращает identity Command Center или None.

    Порт занят чужим приложением — это НЕ повод переиспользовать его как свой
    сервер: окно откроется на чужом UI, а владелец решит, что это BOSSMAN.
    """
    ident = _get_json(base_url.rstrip("/") + "/api/identity", timeout)
    if ident and ident.get("app") == APP_IDENTITY:
        return ident
    return None


def port_busy(base_url: str, timeout: float = 1.5) -> bool:
    """Отвечает ли по адресу хоть что-нибудь (любой HTTP-статус)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base_url, timeout=timeout) as resp:  # noqa: S310
            return resp.status < 600
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def server_alive(url: str, timeout: float = 1.5) -> bool:
    """Совместимость: «на этом адресе уже работает Command Center»."""
    return identify_server(url, timeout) is not None


def open_window(browser: str, url: str, profile_dir: Path, *, extra: Sequence[str] = (),
                window_size: str = "1440,900") -> subprocess.Popen:
    """Запускает процесс окна и сразу возвращает его (для тестов и внешнего управления)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    argv = browser_argv(browser, url, profile_dir, window_size=window_size, extra=extra)
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL)  # noqa: S603 — argv-only, без shell


def _run_log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desktop-run.log"


def _append_run_log(data_dir: Path, message: str) -> None:
    """Постоянный журнал запусков окна (переживает pythonw без консоли)."""
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        with open(_run_log_path(data_dir), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except OSError:
        pass


def _desktop_lock_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desktop.lock"


def _read_lock(data_dir: Path) -> dict | None:
    try:
        data = json.loads(_desktop_lock_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
    p.add_argument("--install-shortcut", action="store_true",
                   help="создать ярлык BOSSMAN на рабочем столе (и в меню «Пуск» на Windows) и выйти")
    p.add_argument("--uninstall-shortcut", action="store_true", help="удалить созданные ярлыки и выйти")
    p.add_argument("--print-launcher", action="store_true", help="показать, что будет записано в ярлык, и выйти")
    return p


def run(argv: Sequence[str] | None = None, *, launcher: Callable[..., int] = launch_window,
        out=sys.stdout) -> int:
    """Точка входа с инъекцией launcher'а для тестов.

    Коды выхода: 0 ок, 2 нет браузера, 3 сервер не поднялся, 4 порт занят чужим
    приложением, 5 не удалось создать ярлык."""
    from .config import settings

    if out is None:
        # pythonw (ярлык BOSSMAN): консоли нет — информационный вывод отбрасываем,
        # иначе первый же print уронил бы запуск двойным кликом.
        out = open(os.devnull, "w", encoding="utf-8")
    else:
        try:
            # Русская консоль Windows (cp1251/cp1252): русские сообщения не должны
            # ронять установщик/запуск кодировочной ошибкой.
            out.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — StringIO в тестах, пайпы и т.п.
            pass
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}/"
    profile_dir = Path(args.profile) if args.profile else Path(settings.data_dir) / "desktop-profile"

    if args.install_shortcut or args.uninstall_shortcut or args.print_launcher:
        from . import desktop_install

        spec = desktop_install.build_spec(host=host, port=port)
        if args.print_launcher:
            print(f"[bcc-desktop] команда ярлыка: {' '.join(spec.argv)}", file=out)
            print(f"[bcc-desktop] рабочий каталог: {spec.workdir}", file=out)
            print(f"[bcc-desktop] значок: {spec.icon}", file=out, flush=True)
            return 0
        if args.uninstall_shortcut:
            removed = desktop_install.uninstall()
            print(f"[bcc-desktop] удалено ярлыков: {len(removed)}", file=out, flush=True)
            for r in removed:
                print(f"  - {r}", file=out, flush=True)
            return 0
        try:
            created = desktop_install.install(spec)
        except Exception as exc:  # noqa: BLE001 — сообщаем владельцу, а не падаем трейсбеком
            print(f"[bcc-desktop] ярлык не создан: {exc}", file=out, flush=True)
            return 5
        print(f"[bcc-desktop] ярлык BOSSMAN создан ({len(created)}):", file=out, flush=True)
        for c in created:
            print(f"  - {c}", file=out, flush=True)
        return 0

    browser = args.browser or find_browser()
    if not browser:
        print("[bcc-desktop] не найден Chromium/Chrome/Edge — укажите --browser или BCC_DESKTOP_BROWSER;"
              " веб-версия остаётся доступной командой `bcc`.", file=out, flush=True)
        return 2

    data_dir = Path(settings.data_dir)
    _append_run_log(data_dir, f"start pid={os.getpid()} url={url}")
    lock = _read_lock(data_dir)
    if lock:
        # Второе окно на том же профиле Chrome не открывает, а молча завершается
        # (profile lock) — и владелец видит «само закрылось». Живой замок = окно
        # уже открыто: второе не запускаем. Мёртвый замок = краш: затираем и идём.
        try:
            lock_port = int(lock.get("port", 0))
        except (TypeError, ValueError):
            lock_port = 0
        if lock_port and identify_server(f"http://{host}:{lock_port}/"):
            msg = (f"[bcc-desktop] окно BOSSMAN уже запущено (порт {lock_port}) — второе окно "
                   "на том же профиле не открываю, иначе Chrome закроется сам")
            print(msg, file=out, flush=True)
            _append_run_log(data_dir, f"refused-second-window existing-port={lock_port}")
            return 0

    started: _BackgroundServer | None = None
    ident = identify_server(url)
    if ident:
        print(f"[bcc-desktop] Command Center уже работает: {url} "
              f"(версия {ident.get('version', '?')}) — подключаюсь к нему", file=out, flush=True)
    elif port_busy(url):
        # Порт занят чужим приложением: второй сервер тут не поднять, а открывать
        # чужой UI под именем BOSSMAN нельзя.
        print(f"[bcc-desktop] порт {port} занят другим приложением (это не Command Center) —"
              f" укажите другой --port", file=out, flush=True)
        return 4
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
    wrote_lock = False
    try:
        try:
            _desktop_lock_path(data_dir).write_text(
                json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
            wrote_lock = True
        except OSError:
            pass
        t0 = time.monotonic()
        try:
            code = launcher(browser, url, profile_dir, extra=tuple(args.browser_arg), window_size=args.window_size)
        finally:
            if started is not None:
                started.stop()
    finally:
        if wrote_lock:
            try:
                _desktop_lock_path(data_dir).unlink()
            except OSError:
                pass
    lifetime = time.monotonic() - t0
    _append_run_log(data_dir, f"browser-exit code={code} lifetime={lifetime:.1f}s url={url}")
    if lifetime < 10:
        print(f"[bcc-desktop] окно закрылось через {lifetime:.1f} c (код {code}) — вероятно, краш "
              f"или второй Chrome на том же профиле. Логи: {_run_log_path(data_dir)} и "
              f"{profile_dir}{os.sep}chrome_debug.log", file=out, flush=True)
    return int(code or 0)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
