"""UX 2.0 — настольное окно (bcc-desktop): реальный предустановленный Chromium в режиме --app
против живого сервера; переиспользование уже запущенного сервера; секрет не попадает в URL."""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest

from bcc import desktop

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import live  # noqa: F401


def test_find_browser_prefers_preinstalled_chromium(tmp_path):
    fake = tmp_path / "chromium"
    fake.write_text("")
    assert desktop.find_browser([str(fake), "definitely-not-a-browser-xyz"]) == str(fake)
    assert desktop.find_browser(["definitely-not-a-browser-xyz", ""]) is None


def test_browser_argv_is_app_window_without_secret(tmp_path):
    argv = desktop.browser_argv("/usr/bin/chromium", "http://127.0.0.1:8800/", tmp_path / "prof")
    assert argv[0] == "/usr/bin/chromium"
    assert "--app=http://127.0.0.1:8800/" in argv
    assert any(a.startswith("--user-data-dir=") and a.endswith("prof") for a in argv)
    assert "--no-first-run" in argv
    with pytest.raises(ValueError):
        desktop.browser_argv("/usr/bin/chromium", "http://127.0.0.1:8800/?token=abc", tmp_path)


def test_run_reuses_running_server_and_does_not_bind_twice(live, tmp_path):  # noqa: F811
    calls: list[dict] = []

    def fake_launcher(browser, url, profile_dir, *, extra=(), window_size="1440,900"):
        calls.append({"browser": browser, "url": url, "profile": Path(profile_dir), "extra": tuple(extra)})
        return 0

    out = io.StringIO()
    code = desktop.run(["--host", "127.0.0.1", "--port", str(live.port), "--browser", "/bin/true",
                        "--profile", str(tmp_path / "prof"), "--browser-arg=--x-test", "--no-show-token"],
                       launcher=fake_launcher, out=out)
    assert code == 0
    assert calls == [{"browser": "/bin/true", "url": f"http://127.0.0.1:{live.port}/", "profile": tmp_path / "prof", "extra": ("--x-test",)}]
    assert "Command Center уже работает" in out.getvalue()
    assert "token" not in out.getvalue().lower()


def test_run_reports_missing_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: None)
    out = io.StringIO()
    assert desktop.run(["--profile", str(tmp_path), "--no-server", "--port", "1"], launcher=lambda *a, **k: 0, out=out) == 2
    assert "не найден" in out.getvalue()


def test_local_health_checks_ignore_proxy_environment(live, monkeypatch):  # noqa: F811
    """Прокси из окружения не должен применяться к 127.0.0.1.

    ALL_PROXY/HTTP(S)_PROXY адресованы внешним провайдерам; если проверка
    «сервер уже работает» унаследует их, она уйдёт на прокси и либо соврёт
    (чужой ответ), либо упадёт. Здесь прокси заведомо мёртв и указывает на
    закрытый порт: живой локальный сервер всё равно должен определяться.
    """
    from .test_ux2_thinking_pane import _free_port, loopback_get

    dead = f"socks5://127.0.0.1:{_free_port()}"
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(var, dead)
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    assert desktop.server_alive(live.url + "/") is True      # запуск окна: сервер найден
    assert loopback_get(live.url + "/").status_code < 500    # готовность в тестах и снимках
    # и никакого «сервера» там, где его нет
    assert desktop.server_alive(f"http://127.0.0.1:{_free_port()}/") is False


def _devtools_endpoint(profile_dir, proc, timeout: float = 60.0):
    """Порт и ws-путь отладки из ``DevToolsActivePort`` в профиле окна.

    Браузер создаёт этот файл только после того, как отладочный сервер начал
    слушать, поэтому файл — сам по себе признак готовности: ни угадывания
    порта, ни гонки, ни опроса чужого адреса. HTTP-эндпоинт ``/json/version``
    здесь сознательно не используется: именно он в CI не отвечал 30 секунд,
    пока браузер был жив и уже печатал «DevTools listening».
    """
    marker = Path(profile_dir) / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        rc = proc.poll()
        if marker.exists():
            seen = marker.read_text(encoding="utf-8", errors="replace")
            lines = seen.splitlines()
            if len(lines) >= 2 and lines[0].strip().isdigit():
                return int(lines[0].strip()), lines[1].strip()
        if rc is not None:
            raise AssertionError(f"окно завершилось, rc={rc}, DevToolsActivePort={seen!r}")
        time.sleep(0.1)
    raise AssertionError(
        f"DevToolsActivePort не появился за {timeout} с (rc={proc.poll()}, содержимое={seen!r})")


@pytest.mark.timeout(120)
@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
def test_real_chromium_app_window_renders_command_center(live, tmp_path):  # noqa: F711
    """Настоящее окно --app с предустановленным Chromium: без дисплея — headless-снимок,
    который доказывает, что команда окна работает и страница входа отрисована."""
    from playwright.sync_api import sync_playwright

    browser = desktop.find_browser()
    assert browser, "предустановленный Chromium не найден"
    profile = tmp_path / "profile"
    # Порт отладки выбирает сам браузер (0) и публикует его в профиле. Тест
    # больше не занимает порт заранее: между выбором и запуском окна ядро могло
    # отдать его другому соединению, и тогда мы опрашивали чужой адрес.
    proc = desktop.open_window(
        browser, live.url + "/", profile,
        extra=("--headless=new", "--no-sandbox", "--disable-gpu",
               "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0"))
    try:
        port, ws_path = _devtools_endpoint(profile, proc)
        # Регрессия к сбою CI: порт получен из DevToolsActivePort, а не угадан.
        assert port > 0 and ws_path.startswith("/devtools/browser/"), (port, ws_path)
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(f"ws://127.0.0.1:{port}{ws_path}")
            info = b.new_browser_cdp_session().send("Browser.getVersion")
            assert "Chrome" in info.get("product", ""), info
            pages = [p for c in b.contexts for p in c.pages]
            page = next((p for p in pages if p.url.startswith(live.url)), None)
            assert page is not None, [p.url for p in pages]
            page.wait_for_selector("#login:not([hidden])", timeout=15000)   # окно показало вход в Command Center
            assert "BOSSMAN" in page.title()
            # войти прямо в окне: cookie сессии остаётся в профиле окна, а не в URL
            page.fill("#login-token", live.svc.auth.token)
            page.click("#login-submit")
            page.wait_for_selector("#shell:not([hidden])", timeout=15000)
            assert "token=" not in page.url
            b.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    assert (tmp_path / "profile").is_dir()  # отдельный профиль окна создан
    # профиль не должен содержать токен доступа в открытом виде
    for f in (tmp_path / "profile").rglob("*"):
        if f.is_file() and f.stat().st_size < 2_000_000:
            try:
                data = f.read_bytes()
            except OSError:
                continue
            assert live.svc.auth.token.encode() not in data, f"токен утёк в {f}"


# ---------------------------------------------------------------- ярлык на рабочем столе

def test_linux_shortcut_is_written_and_valid(tmp_path):
    """Реальная установка ярлыка в подставной HOME: файлы созданы, Exec/Icon/Path заполнены."""
    from bcc import desktop_install

    spec = desktop_install.build_spec(executable="/usr/bin/python3", workdir=tmp_path, port=8800)
    (tmp_path / "Desktop").mkdir()
    created = desktop_install.install(spec, home=tmp_path, system="Linux")
    assert created, "ярлык не создан"
    entry = (tmp_path / ".local/share/applications/bossman.desktop").read_text(encoding="utf-8")
    assert "[Desktop Entry]" in entry
    assert "Name=BOSSMAN" in entry
    assert "-m bcc.desktop" in entry and "--port 8800" in entry
    assert "Terminal=true" in entry          # консоль с токеном открывается вместе с окном
    assert f"Path={tmp_path}" in entry
    icon = tmp_path / ".local/share/icons/hicolor/512x512/apps/bossman.png"
    assert icon.exists() and icon.stat().st_size > 1000, "значок не установлен"
    assert f"Icon={icon}" in entry
    assert "token" not in entry.lower()
    # ярлык на рабочем столе тоже есть и исполняемый
    desk = tmp_path / "Desktop" / "bossman.desktop"
    assert desk.exists() and os.access(desk, os.X_OK)
    removed = desktop_install.uninstall(home=tmp_path, system="Linux")
    assert len(removed) == 3 and not desk.exists()


def test_windows_shortcut_script_quotes_safely(tmp_path):
    """Скрипт .lnk: экранирование кавычек, никакого shell-склеивания, значок .ico."""
    from bcc import desktop_install

    spec = desktop_install.build_spec(executable=r"C:\Py'thon\pythonw.exe", workdir=r"C:\BOSS MAN", port=8801)
    script = desktop_install.powershell_shortcut_script(spec, [r"C:\Users\o\Desktop\BOSSMAN.lnk"])
    assert "WScript.Shell" in script
    assert "'C:\\Py''thon\\pythonw.exe'" in script      # одинарная кавычка удвоена
    assert "-m bcc.desktop --port 8801" in script
    assert "'C:\\BOSS MAN'" in script
    assert ".Save()" in script
    assert "token" not in script.lower()


def test_install_windows_uses_powershell_argv(tmp_path, monkeypatch):
    """Windows-путь вызывает powershell только через argv и уважает код возврата."""
    from bcc import desktop_install

    calls = []
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    spec = desktop_install.build_spec(executable="python.exe", workdir=tmp_path)
    created = desktop_install.install(spec, home=tmp_path, system="Windows",
                                      runner=lambda argv: calls.append(argv) or 0)
    assert len(created) == 2 and created[0].name == "BOSSMAN.lnk"
    assert calls and calls[0][0] == "powershell" and "-NoProfile" in calls[0]
    with pytest.raises(RuntimeError):
        desktop_install.install(spec, home=tmp_path, system="Windows", runner=lambda argv: 1)


def test_run_install_shortcut_exits_zero(tmp_path, monkeypatch):
    """`bcc-desktop --install-shortcut` не запускает ни сервер, ни браузер."""
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: "/bin/true")
    from bcc import desktop_install

    monkeypatch.setattr(desktop_install, "install", lambda spec=None, **kw: [tmp_path / "BOSSMAN.lnk"])
    out = io.StringIO()
    code = desktop.run(["--install-shortcut"], launcher=lambda *a, **k: 99, out=out)
    assert code == 0 and "ярлык BOSSMAN создан" in out.getvalue()


def test_busy_port_with_foreign_app_is_refused(tmp_path):
    """Чужое приложение на порту не выдаётся за Command Center (код 4)."""
    import http.server
    import threading

    class Foreign(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not bossman")

        def log_message(self, *a):  # тишина в тестовом выводе
            return

    from .test_ux2_thinking_pane import _free_port

    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), Foreign)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert desktop.identify_server(f"http://127.0.0.1:{port}/") is None
        assert desktop.port_busy(f"http://127.0.0.1:{port}/") is True
        out = io.StringIO()
        code = desktop.run(["--port", str(port), "--browser", "/bin/true", "--profile", str(tmp_path)],
                           launcher=lambda *a, **k: 0, out=out)
        assert code == 4, out.getvalue()
        assert "занят другим приложением" in out.getvalue()
    finally:
        srv.shutdown()


def test_identity_of_real_server(live):  # noqa: F811
    """У живого Command Center identity действительно есть и он опознаётся."""
    ident = desktop.identify_server(live.url)
    assert ident and ident["app"] == "bossman-command-center"
    assert ident.get("version")
    assert "token" not in str(ident).lower()

def test_second_window_refused_while_first_instance_alive(tmp_path, monkeypatch):
    """?????? ???? ?? ??? ?? ??????? Chrome ????? ??????????? (profile lock).
    ????? ????? + ????? ?????? = ????? ? ???????? ??????????, ??? ??????? Chrome."""
    from bcc.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "desktop.lock").write_text(__import__("json").dumps({"pid": 1, "port": 18923}), encoding="utf-8")
    monkeypatch.setattr(desktop, "identify_server",
                        lambda url, timeout=2.0: {"app": "bossman-command-center"} if ":18923/" in url else None)
    calls = []
    out = io.StringIO()
    code = desktop.run(["--port", "18924", "--browser", "/bin/true", "--profile", str(tmp_path / "prof")],
                       launcher=lambda *a, **k: (calls.append(1), 0)[1], out=out)
    assert code == 0
    assert calls == []
    assert "BOSSMAN" in out.getvalue() and "18923" in out.getvalue()
    assert "browser-exit" not in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_fast_browser_exit_is_logged_with_hint(tmp_path, monkeypatch):
    """????, ??????????? ?? ???????, ????????? ????: ???, ????? ????? ? ???? ????????."""
    from bcc.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: False)

    class _FakeServer:
        def __init__(self, host, port):
            pass

        def start(self, url):
            return True

        def stop(self):
            pass

    monkeypatch.setattr(desktop, "_BackgroundServer", _FakeServer)
    out = io.StringIO()
    code = desktop.run(["--port", "18925", "--browser", "/bin/true", "--profile", str(tmp_path / "prof")],
                       launcher=lambda *a, **k: 1, out=out)
    assert code == 1
    assert "chrome_debug.log" in out.getvalue()
    log = (tmp_path / "desktop-run.log").read_text(encoding="utf-8")
    assert "browser-exit code=1" in log
    assert not (tmp_path / "desktop.lock").exists()


# ------------------------------------------------- консоль с токеном при запуске

def test_launcher_shows_access_token_but_never_writes_it_to_logs(live, tmp_path, monkeypatch):  # noqa: F811
    """Владельцу нужен токен при первом входе, поэтому он печатается в консоль.

    Но `desktop-run.log` владелец пересылает при разборе сбоев (так написано в
    docs/ux/DESKTOP_SELF_CLOSE_AUDIT.md), поэтому секрет туда попасть не должен.
    """
    from bcc.config import settings

    data_dir = tmp_path / "desk"
    data_dir.mkdir(exist_ok=True)
    token = "TESTTOKEN-" + "z" * 24
    (data_dir / desktop.TOKEN_FILE_NAME).write_text(token, encoding="utf-8")
    monkeypatch.setattr(settings, "data_dir", data_dir)

    out = io.StringIO()
    code = desktop.run(["--host", "127.0.0.1", "--port", str(live.port), "--browser", "/bin/true",
                        "--profile", str(tmp_path / "prof")],
                       launcher=lambda *a, **k: 0, out=out)
    assert code == 0
    text = out.getvalue()
    assert token in text                                  # токен виден владельцу
    assert "BOSSMAN — вход в Command Center" in text
    assert str(data_dir / desktop.TOKEN_FILE_NAME) in text  # и путь к файлу

    run_log = (data_dir / "desktop-run.log").read_text(encoding="utf-8")
    assert run_log.strip(), "журнал запусков должен вестись"
    assert token not in run_log, "секрет не должен попадать в пересылаемый журнал"


def test_no_show_token_keeps_the_secret_off_screen(live, tmp_path, monkeypatch):  # noqa: F811
    """Режим без консоли: приложение работает, токен на экран не выводится."""
    from bcc.config import settings

    data_dir = tmp_path / "desk"
    data_dir.mkdir(exist_ok=True)
    token = "TESTTOKEN-" + "q" * 24
    (data_dir / desktop.TOKEN_FILE_NAME).write_text(token, encoding="utf-8")
    monkeypatch.setattr(settings, "data_dir", data_dir)

    out = io.StringIO()
    assert desktop.run(["--host", "127.0.0.1", "--port", str(live.port), "--browser", "/bin/true",
                        "--profile", str(tmp_path / "prof"), "--no-show-token"],
                       launcher=lambda *a, **k: 0, out=out) == 0
    assert token not in out.getvalue()


def test_shortcut_carries_no_secret_and_console_mode_is_explicit(tmp_path):
    """Ярлык не хранит токен: секрет печатает процесс, а не файл ярлыка."""
    from bcc import desktop_install

    console = desktop_install.build_spec(executable="/usr/bin/python3", workdir=tmp_path, port=8800)
    silent = desktop_install.build_spec(executable="/usr/bin/python3", workdir=tmp_path, port=8800,
                                        console=False)
    assert console.console is True and "--no-show-token" not in console.args
    assert silent.console is False and "--no-show-token" in silent.args
    for spec in (console, silent):
        blob = " ".join(spec.argv) + desktop_install.desktop_entry(spec)
        assert "token" not in blob.lower().replace("--no-show-token", "")


def test_console_python_is_chosen_on_windows(tmp_path):
    """На Windows консольный режим берёт python.exe вместо pythonw.exe."""
    from bcc import desktop_install

    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    pyw, py = str(tmp_path / "pythonw.exe"), str(tmp_path / "python.exe")
    assert desktop_install._console_python(pyw, windows=True) == py
    assert desktop_install._windowless_python(py, windows=True) == pyw
    # не Windows — интерпретатор не подменяется
    assert desktop_install._console_python(pyw, windows=False) == pyw


def test_access_banner_warns_only_when_console_owns_the_app():
    """Предупреждение «не закрывайте консоль» уместно только когда сервер наш."""
    from pathlib import Path as _P

    owns = desktop.access_banner("http://127.0.0.1:8800/", "T", _P("/d/token"), console_owns_app=True)
    attached = desktop.access_banner("http://127.0.0.1:8800/", "T", _P("/d/token"), console_owns_app=False)
    assert "закрывать нельзя" in owns
    assert "закрывать нельзя" not in attached
    assert desktop.access_banner("http://x/", None, _P("/d/token"), console_owns_app=False).count("Токен") >= 1
