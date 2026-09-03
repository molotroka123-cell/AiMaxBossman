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
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

# Один и тот же переключатель для баннера окна и для анонса токена сервером.
from .auth import TOKEN_STDOUT_ENV

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


def _registry_browser(entry: str) -> str | None:
    """Путь браузера из реестра Windows (64/32-битные кусты), None если нет."""
    if os.name != "nt":
        return None
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, entry) as key:
                value, _ = winreg.QueryValueEx(key, None)
            if isinstance(value, str) and value and Path(value).exists():
                return value
        except OSError:
            continue
    return None


def _windows_browser_candidates() -> tuple[str, ...]:
    """Системные браузеры Windows по стандартным путям и реестру."""
    found: list[str] = []
    paths = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    )
    for p in paths:
        if Path(p).exists() and p not in found:
            found.append(p)
    reg_entries = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
    )
    for entry in reg_entries:
        p = _registry_browser(entry)
        if p and p not in found:
            found.append(p)
    return tuple(found)


def find_browser(candidates: Sequence[str] = BROWSER_CANDIDATES) -> str | None:
    """Первый существующий Chromium-совместимый браузер (абсолютный путь) или None."""
    ordered = list(candidates)
    if os.name == "nt" and candidates is BROWSER_CANDIDATES:
        # Системные браузеры Windows впереди фиксированных путей: реестр знает
        # актуальный путь даже на 32-битной Windows/портативных установках.
        # Явный список кандидатов (тесты, вызовы с параметром) НЕ переставляем.
        sys_browsers = _windows_browser_candidates()
        if sys_browsers:
            ordered = list(sys_browsers) + [c for c in ordered
                                            if c and c not in sys_browsers]
    for cand in ordered:
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


def _record_launch(data_dir: Path, launch_id: str, reason: str, *, code: int | None = None,
                   detail: str = "", browser: str = "", lifetime: float | None = None) -> None:
    """Исход запуска — в журнал тестового периода, а не только в desktop-run.log.

    desktop-run.log в git не уезжает: если окно не открылось совсем, владелец
    присылает журнал, в котором про этот запуск нет ни строчки. Здесь тот же
    исход ложится в общий jsonl и уедет со следующей удачной сессией.

    Причина — код из закрытого списка, а не свободный текст: для кода 3 в
    журнал уходит текст uvicorn, для кода 6 — текст OSError с путём к браузеру.
    Такое кладём отдельным полем, урезанным и с заменой домашнего каталога.
    """
    try:
        from .features import testing_period

        payload: dict = {"launch_id": launch_id, "reason": reason, "pid": os.getpid()}
        if code is not None:
            payload["code"] = code
        if browser:
            payload["browser"] = os.path.basename(browser) or "?"
        if lifetime is not None:
            payload["lifetime_s"] = round(lifetime, 1)
        if detail:
            payload["detail"] = testing_period.mask_home(str(detail))[:200]
        testing_period.record_launch(data_dir, "desktop.launch", payload)
    except Exception:  # noqa: BLE001 — журнал не имеет права мешать запуску
        pass


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


GUI_CONSOLE_LOG_NAME = "desktop-console.log"


def _gui_console_stream(data_dir: Path):
    """Файл вместо отсутствующей консоли. ``None``, если писать некуда."""
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        return open(_gui_console_log_path(data_dir), "a", encoding="utf-8",
                    errors="replace", buffering=1)
    except OSError:
        return None


def _gui_console_log_path(data_dir: Path) -> Path:
    return Path(data_dir) / GUI_CONSOLE_LOG_NAME


def bind_missing_std_streams(data_dir: Path):
    """Под ``pythonw`` (ярлык без консоли) ``sys.stdout``/``sys.stderr`` равны ``None``.

    Тогда любой ``print`` — наш, uvicorn'а или чужой библиотеки — падает
    ``AttributeError: 'NoneType' object has no attribute 'write'``, и запуск
    двойным кликом умирает молча. Подставляем файловый журнал в ``data_dir``
    ДО того, как что-либо начнёт писать: логи есть, упасть некуда.

    Возвращает подставленный поток или ``None``, если консоль на месте.
    """
    missing = [name for name in ("stdout", "stderr") if getattr(sys, name, None) is None]
    if not missing:
        return None
    stream = _gui_console_stream(data_dir)
    if stream is None:
        # Каталог данных недоступен — важно всё равно не оставить None.
        stream = open(os.devnull, "w", encoding="utf-8")
    else:
        _append_run_log(data_dir, f"gui-console -> {_gui_console_log_path(data_dir)}")
        stream.write(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%S')} запуск без консоли, pid={os.getpid()} ===\n")
    for name in missing:
        setattr(sys, name, stream)
    return stream


TOKEN_FILE_NAME = "token"


def read_access_token(data_dir: Path) -> str | None:
    """Токен доступа из файла инсталляции. Не создаёт его и не пишет в лог.

    Файл заводит сам сервер (``TokenAuth``) при первом старте, поэтому читаем
    ПОСЛЕ того, как сервер поднялся или опознан на порту.
    """
    path = Path(data_dir) / TOKEN_FILE_NAME
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def access_banner(url: str, token: str | None, token_path: Path, *, console_owns_app: bool) -> str:
    """Рамка со входом для владельца.

    Токен показывается только здесь, в живой консоли: в ``desktop-run.log`` его
    нет (журнал владелец пересылает при разборе сбоев), в ярлыке и в аргументах
    процесса — тоже нет.
    """
    line = "=" * 68
    body = [line, "  BOSSMAN — вход в Command Center", "", f"  Адрес:  {url}"]
    if token:
        body += [f"  Токен:  {token}"]
    else:
        body += ["  Токен:  файл ещё не создан — появится при первом старте сервера"]
    body += [
        f"  Файл:   {token_path}",
        "",
        "  Токен нужен один раз: дальше вход помнит cookie в профиле окна.",
    ]
    if console_owns_app:
        body += ["  Это окно консоли закрывать нельзя — вместе с ним закроется BOSSMAN."]
    body += [line]
    return "\n".join(body)


def _pause_console(out) -> None:
    """Не дать окну консоли закрыться раньше, чем владелец прочитает причину.

    Ярлык запускает python.exe: как только процесс вышел, консоль исчезает
    вместе с сообщением об ошибке. Ждём Enter только в настоящей консоли —
    в тестах, пайпах и под pythonw stdin не интерактивен, и пауза не сработает.
    """
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return
    except (AttributeError, ValueError, OSError):
        return
    try:
        print("\n[bcc-desktop] нажмите Enter, чтобы закрыть это окно…", file=out, flush=True)
        sys.stdin.readline()
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        pass


def _desktop_lock_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desktop.lock"


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с этим pid.

    На Windows ``os.kill(pid, 0)`` НЕ безобидная проверка: он вызывает
    TerminateProcess и убил бы чужой процесс, поэтому там идём через
    OpenProcess + WaitForSingleObject.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT — ещё работает
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 — не смогли проверить: считаем мёртвым, замок не должен запирать
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # чужой процесс, но живой
    except OSError:
        return False
    return True


def _read_lock(data_dir: Path) -> dict | None:
    try:
        data = json.loads(_desktop_lock_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


#: Код выхода: окно не пережило отведённое на старт время.
STARTUP_FAILED_CODE = 124


def launch_window(browser: str, url: str, profile_dir: Path, *, extra: Sequence[str] = (),
                  window_size: str = "1440,900", timeout: float | None = None,
                  ready: Callable[[], bool] | None = None) -> int:
    """Открывает окно и ждёт, пока владелец сам его закроет.

    Таймаут здесь — **только про старт**, а не про срок жизни окна. Раньше он
    уходил прямо в ``proc.wait(timeout=...)``, и по его истечении окно
    получало terminate и kill: исправно работающее окно закрывалось само через
    заданное время, потому что владелец им пользовался дольше таймаута. Это и
    был дефект — таймаут старта не имеет права трогать открытое окно.

    Теперь так:

    * окно, пережившее отведённое на старт время, считается запущенным и ждётся
      БЕЗ предела — закрыть его может только владелец;
    * окно, умершее раньше срока, возвращает свой код выхода: это и есть
      «не открылось», и причину разбирает вызывающий по коду и времени жизни;
    * если вызывающий умеет проверить готовность (``ready``), опрос идёт до
      дедлайна: подтверждённое окно дальше живёт без предела, а неподтверждённое
      завершается аккуратно (terminate, затем kill) с кодом
      ``STARTUP_FAILED_CODE``.

    Граница честности: «окно живо, но пустое» одним таймаутом не отличить от
    «окно живо и работает». Без ``ready`` такой случай сюда не приходит вовсе —
    его разбирают диагностика пустого окна и сторож, а не убийство по времени.
    """
    proc = open_window(browser, url, profile_dir, extra=extra, window_size=window_size)
    if not timeout:
        return proc.wait()

    deadline = time.monotonic() + timeout
    confirmed = ready is None
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return code                      # умерло само — это и есть «не открылось»
        if not confirmed and ready is not None and ready():
            confirmed = True
            break
        time.sleep(0.2)

    if not confirmed:
        # Готовность проверяема и не подтвердилась — только теперь завершаем.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return STARTUP_FAILED_CODE
    return proc.wait()                        # старт состоялся: дальше решает владелец


class _BackgroundServer:
    """uvicorn в потоке — ровно тот же app, что у ``bcc``; останавливается вместе с окном."""

    def __init__(self, host: str, port: int) -> None:
        import uvicorn

        from .app import create
        from .config import settings

        settings.ensure_dirs()
        self.server = uvicorn.Server(uvicorn.Config(create(), host=host, port=port, log_level="warning"))
        self.error: str | None = None
        self.thread = threading.Thread(target=self._serve, name="bcc-desktop-server", daemon=True)

    def _serve(self) -> None:
        """Ошибка потока (обычно занятый порт) должна дойти до владельца.

        Раньше поток умирал молча: `start()` возвращал False, `run()` печатал
        «сервер не поднялся» без причины, и в журнале не оставалось ничего.
        """
        try:
            self.server.run()
        except BaseException as exc:  # noqa: BLE001 — SystemExit из uvicorn тоже сюда
            self.error = f"{type(exc).__name__}: {exc}"

    def start(self, url: str, timeout: float = 30.0) -> bool:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if server_alive(url):
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.2)
        self.error = self.error or "сервер не ответил за %.0f с" % timeout
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
    p.add_argument("--window-timeout", type=float, default=None,
                   help="максимум секунд ожидания окна (по умолчанию BCC_APP_STARTUP_TIMEOUT, иначе без таймаута)")
    p.add_argument("--status", action="store_true",
                   help="показать состояние запуска (desktop.lock + сервер) и выйти")
    p.add_argument("--web", action="store_true",
                   help="открыть веб-версию в системном браузере (без --app-окна)")
    p.add_argument("--install-shortcut", action="store_true",
                   help="создать ярлык BOSSMAN на рабочем столе (и в меню «Пуск» на Windows) и выйти")
    p.add_argument("--uninstall-shortcut", action="store_true", help="удалить созданные ярлыки и выйти")
    p.add_argument("--print-launcher", action="store_true", help="показать, что будет записано в ярлык, и выйти")
    p.add_argument("--show-token", dest="show_token", action="store_true", default=True,
                   help="показать токен доступа в консоли при запуске (по умолчанию да)")
    p.add_argument("--no-show-token", dest="show_token", action="store_false",
                   help="не показывать токен в консоли")
    p.add_argument("--console", dest="console", action="store_true", default=True,
                   help="ярлык открывает окно консоли с токеном (по умолчанию да)")
    p.add_argument("--no-console", dest="console", action="store_false",
                   help="ярлык запускает приложение без окна консоли")
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

    if args.status:
        # Диагностика для владельца без CDP: что говорит desktop.lock и жив ли сервер.
        data_dir = Path(settings.data_dir)
        lock = _read_lock(data_dir)
        state = {"lock": lock, "server_alive": server_alive(url),
                 "port": port, "url": url}
        print(json.dumps(state, ensure_ascii=False, indent=1), file=out, flush=True)
        return 0

    if args.install_shortcut or args.uninstall_shortcut or args.print_launcher:
        from . import desktop_install

        spec = desktop_install.build_spec(host=host, port=port, console=args.console)
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

    data_dir = Path(settings.data_dir)
    browser = args.browser or find_browser()
    if not browser:
        print("[bcc-desktop] не найден Chromium/Chrome/Edge — укажите --browser или BCC_DESKTOP_BROWSER;"
              " веб-версия остаётся доступной командой `bcc`.", file=out, flush=True)
        _append_run_log(data_dir, "exit code=2 no-browser-found")
        _record_launch(data_dir, "-", "no-browser-found", code=2)
        _pause_console(out)
        return 2

    # Воспроизведено на Windows: Chromium из кэша Playwright (`%LOCALAPPDATA%\\ms-playwright`,
    # `/opt/pw-browsers`) в режиме --app НЕ загружает страницу — окно открывается пустым,
    # запрос к серверу не уходит. Системный Chrome/Edge в тех же условиях работает.
    # Сам браузер работоспособен (headless/CDP отвечают) — это ограничение сборки в --app.
    _PW_MARKERS = ("ms-playwright", "pw-browsers", "playwright")
    if any(m in str(browser).lower() for m in _PW_MARKERS):
        _append_run_log(data_dir, "warning playwright-browser-binary-detected "
                                  "(--app window may stay blank)")
        print(f"[bcc-desktop] ВНИМАНИЕ: браузер {browser} — сборка из кэша Playwright. "
              "В режиме --app такое окно может открыться ПУСТЫМ (страница не загружается).\n"
              "[bcc-desktop] Укажите системный Chrome/Edge: —browser \"C:\\Program Files\\"
              "Google\\Chrome\\Application\\chrome.exe\" "
              "(или BCC_DESKTOP_BROWSER). Веб-версия: `bcc`.", file=out, flush=True)
        _pause_console(out)

    launch_id = uuid.uuid4().hex[:8]
    _append_run_log(data_dir, f"start pid={os.getpid()} launch={launch_id} url={url}")
    _record_launch(data_dir, launch_id, "start", browser=browser)
    lock = _read_lock(data_dir)
    if lock:
        # Второе окно на том же профиле Chrome не открывает, а молча завершается
        # (profile lock) — и владелец видит «само закрылось». Живой замок = окно
        # уже открыто: второе не запускаем. Мёртвый замок = краш: затираем и идём.
        try:
            lock_port = int(lock.get("port", 0))
        except (TypeError, ValueError):
            lock_port = 0
        try:
            lock_pid = int(lock.get("pid", 0))
        except (TypeError, ValueError):
            lock_pid = 0
        # Убитый запуск (Stop-Process, закрытая консоль) не проходит finally и
        # оставляет замок с мёртвым pid. Одной проверки порта мало: на порту
        # может сидеть посторонний сервер, и тогда guard запирал бы окно навсегда.
        owner_alive = _pid_alive(lock_pid)
        if not owner_alive:
            _append_run_log(data_dir, f"stale-lock-cleared pid={lock_pid} port={lock_port}")
            try:
                _desktop_lock_path(data_dir).unlink()
            except OSError:
                pass
        if owner_alive and lock_port and identify_server(f"http://{host}:{lock_port}/"):
            msg = (f"[bcc-desktop] окно BOSSMAN уже запущено (порт {lock_port}) — второе окно "
                   "на том же профиле не открываю, иначе Chrome закроется сам.\n"
                   "[bcc-desktop] Совет: если окно ПУСТОЕ (страница не загрузилась) — "
                   "закройте его полностью и запустите BOSSMAN заново.")
            print(msg, file=out, flush=True)
            _append_run_log(data_dir, f"refused-second-window existing-port={lock_port}")
            _record_launch(data_dir, launch_id, "refused-second-window", code=0)
            _pause_console(out)
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
        _append_run_log(data_dir, f"exit code=4 port-busy-foreign port={port}")
        _record_launch(data_dir, launch_id, "port-busy-foreign", code=4)
        _pause_console(out)
        return 4
    elif args.no_server:
        print(f"[bcc-desktop] сервер по адресу {url} не отвечает, а --no-server задан", file=out, flush=True)
        _append_run_log(data_dir, "exit code=3 no-server-flag")
        _record_launch(data_dir, launch_id, "no-server-flag", code=3)
        _pause_console(out)
        return 3
    else:
        started = _BackgroundServer(host, port)
        if not started.start(url):
            reason = started.error or "причина неизвестна"
            print(f"[bcc-desktop] сервер не поднялся на {url}: {reason}", file=out, flush=True)
            _append_run_log(data_dir, f"exit code=3 server-start-failed {reason}")
            _record_launch(data_dir, launch_id, "server-start-failed", code=3, detail=reason)
            started.stop()
            _pause_console(out)
            return 3
        print(f"[bcc-desktop] сервер запущен: {url}", file=out, flush=True)

    if args.show_token:
        # Консоль принадлежит приложению только когда сервер поднят этим же
        # процессом: при подключении к уже работающему серверу закрытие консоли
        # его не убивает.
        print(access_banner(url, read_access_token(data_dir), Path(data_dir) / TOKEN_FILE_NAME,
                            console_owns_app=started is not None), file=out, flush=True)

    if args.web:
        # Веб-версия в системном браузере (без --app-окна): сервер поднимается
        # этим же процессом и живёт столько же, сколько живёт процесс.
        if started is None:
            started = _BackgroundServer(host, port)
            if not started.start(url):
                reason = started.error or "причина неизвестна"
                print(f"[bcc-desktop] сервер не поднялся на {url}: {reason}", file=out, flush=True)
                _append_run_log(data_dir, f"exit code=3 server-start-failed {reason}")
                _record_launch(data_dir, launch_id, "server-start-failed", code=3, detail=reason)
                started.stop()
                _pause_console(out)
                return 3
            print(f"[bcc-desktop] сервер запущен: {url}", file=out, flush=True)
        try:
            import webbrowser
            opened = webbrowser.open(url)
            print(f"[bcc-desktop] открываю веб-версию в браузере: {url} "
                  f"({'ок' if opened else 'не удалось — откройте вручную'})", file=out, flush=True)
        except KeyboardInterrupt:
            pass
        try:
            while started.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        started.stop()
        return 0

    print(f"[bcc-desktop] окно: {browser} (профиль {profile_dir})", file=out, flush=True)
    # До try: иначе неожиданная ошибка внутри оставит t0 несвязанным, и вместо
    # причины владелец получил бы UnboundLocalError уже после finally.
    t0 = time.monotonic()
    wrote_lock = False
    try:
        try:
            _desktop_lock_path(data_dir).write_text(
                json.dumps({"pid": os.getpid(), "port": port,
                            "window_opened_at": time.strftime("%Y-%m-%dT%H:%M:%S")}),
                encoding="utf-8")
            wrote_lock = True
        except OSError:
            pass
        # Полная команда окна в журнал: если окно не появилось, из лога видно
        # чем именно и с какими флагами мы его запускали (секретов в argv нет).
        try:
            _append_run_log(data_dir, "browser-argv " + " ".join(
                browser_argv(browser, url, profile_dir, window_size=args.window_size,
                             extra=tuple(args.browser_arg))))
        except Exception:  # noqa: BLE001 — журнал не должен мешать запуску
            pass
        launch_error: OSError | None = None
        try:
            # --window-timeout / BCC_APP_STARTUP_TIMEOUT: если окно не открылось
            # за это время, launch_window() возвращает 124 вместо вечного ожидания.
            win_timeout = args.window_timeout
            if win_timeout is None:
                try:
                    win_timeout = float(os.environ.get("BCC_APP_STARTUP_TIMEOUT", "") or 0) or None
                except ValueError:
                    win_timeout = None
            code = launcher(browser, url, profile_dir, extra=tuple(args.browser_arg),
                            window_size=args.window_size,
                            **({"timeout": win_timeout} if win_timeout else {}))
        except OSError as exc:
            # Раньше это улетало трейсбеком и консоль закрывалась вместе с ним:
            # владелец видел «открылась только командная строка» без причины.
            launch_error, code = exc, 6
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
    if launch_error is not None:
        _append_run_log(data_dir, f"browser-launch-failed {type(launch_error).__name__}: {launch_error}")
        _record_launch(data_dir, launch_id, "browser-launch-failed", code=6,
                       detail=type(launch_error).__name__, browser=browser)
        print(f"[bcc-desktop] не удалось запустить браузер {browser}: {launch_error}\n"
              f"[bcc-desktop] проверьте путь или задайте свой: --browser \"C:\\путь\\chrome.exe\"",
              file=out, flush=True)
        _pause_console(out)
        return 6
    _append_run_log(data_dir, f"browser-exit code={code} lifetime={lifetime:.1f}s url={url}")
    _record_launch(data_dir, launch_id,
                   "window-not-ready" if code == STARTUP_FAILED_CODE else "ok",
                   code=int(code or 0), lifetime=lifetime, browser=browser)
    if code == 124:
        print(f"[bcc-desktop] окно не открылось за отведённое время ({win_timeout or 'BCC_APP_STARTUP_TIMEOUT'} c) — "
              "вероятно, браузер не смог загрузить страницу (см. chrome_debug.log в профиле окна).\n"
              f"[bcc-desktop] Совет: закройте все окна Chrome и запустите снова; при пустом окне "
              f"откройте {url} в обычном браузере вручную (токен в консоли выше).", file=out, flush=True)
        _pause_console(out)
    elif lifetime < 10:
        print(f"[bcc-desktop] окно закрылось через {lifetime:.1f} c (код {code}) — вероятно, краш "
              f"или второй Chrome на том же профиле. Логи: {_run_log_path(data_dir)} и "
              f"{profile_dir}{os.sep}chrome_debug.log", file=out, flush=True)
        _pause_console(out)
    return int(code or 0)


def main() -> None:
    from .config import settings

    argv = list(sys.argv[1:])
    # `bcc-open` — тот же лаунчер, но веб-версия в системном браузере
    # (без --app-окна Chromium).
    if os.path.basename(sys.argv[0]).lower().startswith("bcc-open"):
        argv.insert(0, "--web")
    stream = bind_missing_std_streams(Path(settings.data_dir))
    if stream is None or sys.stdout is not stream:
        # Консоль на месте (или подменён только stderr) — владелец читает баннер сам.
        sys.exit(run(argv))
    # Консоли нет: баннер читать некому, а этот журнал владелец пересылает при
    # разборе сбоя. Токен остаётся только в своём файле с правами 600.
    if "--show-token" in argv:
        # Владелец попросил токен явно — печатаем, но под его ответственность.
        sys.exit(run(argv, out=stream))
    os.environ[TOKEN_STDOUT_ENV] = "0"
    argv.append("--no-show-token")
    sys.exit(run(argv, out=stream))


if __name__ == "__main__":
    main()
