"""UX 2.0 — панель «Процесс работы»: реальный Chromium против реального сервера.
Проверяем, что владелец видит факты исполнения (состояние, шаг, модель,
инструмент, ожидание, повторы, ошибки, прошедшее время) из живого потока
событий, что панель открывается кнопкой/Ctrl+. и запоминает состояние,
и что консоль браузера без ошибок."""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

from bcc.app import create_app
from bcc.config import Settings

from .browser_support import chromium_available, reason as browser_reason

try:
    from bcc.features.browser import CHROMIUM as _CHROMIUM
except Exception:  # noqa: BLE001
    _CHROMIUM = ""


def _launch(pw):
    """Тот же предустановленный Chromium, что использует рантайм (без скачивания)."""
    if _CHROMIUM and Path(_CHROMIUM).exists():
        return pw.chromium.launch(executable_path=_CHROMIUM)
    return pw.chromium.launch()

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """uvicorn в отдельном потоке со своим циклом: тест может публиковать события в шину."""

    def __init__(self, tmp_path: Path) -> None:
        import uvicorn
        data = tmp_path / "data"
        self.settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}", ui_dir=UI_DIR)
        self.app = create_app(self.settings, announce_token=False, start_workers=False)
        self.svc = self.app.state.svc
        self.port = _free_port()
        self.loop = asyncio.new_event_loop()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", loop="none", timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.serve())

    def start(self) -> "LiveServer":
        self.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/", timeout=1).status_code < 500:
                    return self
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def emit(self, kind: str, **data) -> None:
        asyncio.run_coroutine_threadsafe(self.svc.bus.emit(kind, **data), self.loop).result(timeout=5)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def restart(self) -> "LiveServer":
        """Перезапуск «процесса»: новый app на тех же data_dir/БД (токен и сессии браузера
        хранятся в БД и переживают рестарт), тот же порт, новый цикл событий."""
        import uvicorn
        if self.thread.is_alive():
            self.stop()
        self.app = create_app(self.settings, announce_token=False, start_workers=False)
        self.svc = self.app.state.svc
        self.loop = asyncio.new_event_loop()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", loop="none", timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self._run, daemon=True)
        return self.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def live(tmp_path):
    srv = LiveServer(tmp_path).start()
    try:
        yield srv
    finally:
        srv.stop()


def _login(page, srv: LiveServer) -> None:
    page.goto(srv.url + "/", wait_until="domcontentloaded")
    page.fill("#login-token", srv.svc.auth.token)
    page.click("#login-submit")
    page.wait_for_selector("#shell:not([hidden])", timeout=15000)


def test_thinking_pane_shows_live_execution_facts(live):
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        assert page.is_hidden("#think-pane")
        page.click("#think-open")
        page.wait_for_selector("#think-pane:not([hidden])")
        assert page.get_attribute("#think-open", "aria-pressed") == "true"
        page.wait_for_selector("#conn-dot.dot-ok", timeout=15000)                      # WS live

        live.emit("task.started", task_id=1, run_id=7)
        live.emit("task.progress", task_id=1, run_id=7, step=2, max_steps=5, model="qwen-14b", tool_calls=["fs.read"])
        live.emit("tool.called", task_id=1, run_id=7, tool="fs.read", source="core", ok=True, duration_ms=120)
        live.emit("router.fallback", task_id=1, run_id=7, model_id=3, reason="timeout")
        live.emit("run.log", run_id=7, level="info", message="step 2 done")
        live.emit("task.progress", task_id=1, run_id=7, waiting_approval=True, tool="terminal.run")

        card = page.locator('.bx-think-card[data-run="7"]')
        card.wait_for(timeout=10000)
        text = card.inner_text()
        for fragment in ("qwen-14b", "fs.read", "2 из 5", "решение владельца (terminal.run)"):
            assert fragment in text, text
        assert card.get_attribute("data-state") == "waiting_approval"
        grid = card.locator(".bx-think-grid b").all_inner_texts()
        assert grid[5] == "1" and grid[6] == "0", grid                                   # retries / errors
        first = card.locator(".bx-think-elapsed").inner_text()
        page.wait_for_timeout(1300)
        assert card.locator(".bx-think-elapsed").inner_text() != first                    # live elapsed timer
        rows = page.locator(".bx-think-row").all_inner_texts()
        assert len(rows) >= 6 and any("step 2 done" in r for r in rows) and any("запасная модель" in r for r in rows)
        assert "chain" not in "\n".join(rows).lower()

        page.keyboard.press("Control+.")
        page.wait_for_selector("#think-pane[hidden]", state="attached")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#shell:not([hidden])", timeout=15000)
        assert page.is_hidden("#think-pane")                                               # closed state remembered
        page.keyboard.press("Control+.")
        page.wait_for_selector("#think-pane:not([hidden])")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#shell:not([hidden])", timeout=15000)
        page.wait_for_selector("#think-pane:not([hidden])")                                # open state remembered
        browser.close()
    assert errors == [], errors


def test_thinking_pane_stays_bounded_and_fast_under_a_long_stream(live):
    """Долгий поток событий: память и DOM ограничены, перерисовок меньше, чем
    событий, интерфейс не деградирует (это и есть «панель не тормозит»)."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.evaluate("window.__bxThinking.open()")
        page.wait_for_selector("#think-pane:not([hidden])", timeout=10000)

        def responsiveness_ms() -> float:
            return page.evaluate("""() => { const t = performance.now();
                document.getElementById('think-pane').getBoundingClientRect();
                return performance.now() - t; }""")

        base = max(responsiveness_ms(), 0.01)
        total = 900
        for i in range(total):
            live.emit("run.log", run_id=1, task_id=1, level="info", message=f"шаг {i}")
        page.wait_for_timeout(1500)

        stats = page.evaluate("window.__bxThinking.stats()")
        assert stats["events"] <= stats["maxEvents"], stats      # память ограничена
        assert stats["rows"] <= 80, stats                        # DOM ограничен
        assert stats["renders"] < total / 2, stats               # перерисовки склеены в кадры
        after = responsiveness_ms()
        assert after < base + 50, f"панель деградировала: было {base:.2f} мс, стало {after:.2f} мс"

        # вторая волна не растит ни память, ни DOM
        for i in range(300):
            live.emit("run.log", run_id=1, task_id=1, level="info", message=f"ещё {i}")
        page.wait_for_timeout(800)
        stats2 = page.evaluate("window.__bxThinking.stats()")
        assert stats2["events"] <= stats["maxEvents"] and stats2["rows"] <= 80, stats2
        browser.close()

    assert errors == [], errors
