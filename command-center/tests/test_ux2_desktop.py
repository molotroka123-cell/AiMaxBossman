"""UX 2.0 — настольное окно (bcc-desktop): реальный предустановленный Chromium в режиме --app
против живого сервера; переиспользование уже запущенного сервера; секрет не попадает в URL."""
from __future__ import annotations

import io
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
    assert "сервер уже работает" in out.getvalue()
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
