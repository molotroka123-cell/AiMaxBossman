"""UX 2.0 — настольное окно (bcc-desktop): реальный предустановленный Chromium в режиме --app
против живого сервера; переиспользование уже запущенного сервера; секрет не попадает в URL."""
from __future__ import annotations

import io
import os
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
                        "--profile", str(tmp_path / "prof"), "--browser-arg=--x-test"],
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


@pytest.mark.timeout(120)
@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
def test_real_chromium_app_window_renders_command_center(live, tmp_path):  # noqa: F711
    """Настоящее окно --app с предустановленным Chromium: без дисплея — headless-снимок,
    который доказывает, что команда окна работает и страница входа отрисована."""
    import json
    import time
    import urllib.request

    from playwright.sync_api import sync_playwright

    from .test_ux2_thinking_pane import _free_port

    browser = desktop.find_browser()
    assert browser, "предустановленный Chromium не найден"
    cdp = _free_port()
    proc = desktop.open_window(
        browser, live.url + "/", tmp_path / "profile",
        extra=("--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={cdp}"))
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.time() + 30
        version = None
        while time.time() < deadline and proc.poll() is None:
            try:
                version = json.loads(opener.open(f"http://127.0.0.1:{cdp}/json/version", timeout=1).read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        assert version and "Chrome" in version.get("Browser", ""), f"окно не поднялось: {version}, rc={proc.poll()}"
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp}")
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
    assert "Terminal=false" in entry
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
