"""R6: кнопка «СТОП»/«Продолжить» Computer Use в настоящем Chromium.

Серверную семантику (гонки, очередь, перезапуск) проверяет
test_computer_stop_race.py. Здесь — то, что делает владелец: видит плашку,
пока модель управляет компьютером, жмёт «СТОП» с клавиатуры, видит
«остановлено», жмёт «Продолжить». Реальный рабочий стол не трогается:
активность Computer Use задаётся состоянием сервера, а не мышью.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bcc.features import tools_computer as tc

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _proof_dir(tmp_path: Path) -> Path:
    out = Path(os.environ.get("BOSSMAN_CU_STOP_EVIDENCE_DIR") or tmp_path / "proof")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _mark_active(srv) -> None:
    """Модель «только что» смотрела на экран — как после computer.observe."""
    st = tc._base_state(srv.svc)
    st.last_activity_at = time.time()
    srv.emit("computer.observe", generation=1, window="Блокнот", elements=3, screenshot=None)


def test_stop_button_appears_calls_endpoint_and_reflects_state(live, tmp_path, monkeypatch):  # noqa: F811
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    proof = _proof_dir(tmp_path)
    errors: list[str] = []
    posts: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: posts.append(f"{r.method} {r.url.split('/api/')[-1]} "
                                                   f"csrf={bool(r.headers.get('x-bcc-csrf'))}")
                if "/api/computer/" in r.url and r.method == "POST" else None)
        _login(page, live)
        page.wait_for_selector("#cu-stop-panel", state="attached", timeout=15000)
        page.wait_for_selector("#conn-dot.dot-ok", timeout=15000)

        # Простой: модели на рабочем столе нет — плашки нет.
        assert page.get_attribute("#cu-stop-panel", "data-state") == "hidden"
        assert page.is_hidden("#cu-stop-panel")

        # Модель начала управлять — плашка появляется по событию шины.
        _mark_active(live)
        page.wait_for_selector("#cu-stop-panel[data-state='active']", timeout=10000)
        assert page.is_visible("#cu-stop-btn") and page.is_hidden("#cu-resume-btn")
        assert "управляет" in page.text_content("#cu-stop-status")
        page.screenshot(path=str(proof / "cu-stop-active.png"))

        # СТОП с клавиатуры: Tab-фокус на кнопку и Enter.
        page.focus("#cu-stop-btn")
        page.keyboard.press("Enter")
        page.wait_for_selector("#cu-stop-panel[data-state='stopped']", timeout=10000)
        assert live.svc._computer_state.stop.is_set()
        assert (live.settings.data_dir / "computer" / "STOP").exists()
        assert page.is_visible("#cu-resume-btn") and page.is_hidden("#cu-stop-btn")
        assert "ОСТАНОВЛЕНО" in page.text_content("#cu-stop-status")
        # фокус не потерян: он перешёл на «Продолжить»
        assert page.evaluate("() => document.activeElement && document.activeElement.id") == "cu-resume-btn"
        page.screenshot(path=str(proof / "cu-stop-stopped.png"))

        # Перезагрузка страницы — остановка по-прежнему видна (живёт на сервере).
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#cu-stop-panel[data-state='stopped']", timeout=15000)

        # Продолжить — пробелом.
        page.focus("#cu-resume-btn")
        page.keyboard.press("Space")
        page.wait_for_selector("#cu-stop-panel[data-state='active']", timeout=10000)
        assert not live.svc._computer_state.stop.is_set()
        assert not (live.settings.data_dir / "computer" / "STOP").exists()
        browser.close()

    assert posts == ["POST computer/stop csrf=True", "POST computer/resume csrf=True"], posts
    assert errors == [], errors


def test_stopped_state_survives_server_restart_in_ui(live, monkeypatch):  # noqa: F811
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _login(page, live)
        _mark_active(live)
        page.wait_for_selector("#cu-stop-panel[data-state='active']", timeout=15000)
        page.click("#cu-stop-btn")
        page.wait_for_selector("#cu-stop-panel[data-state='stopped']", timeout=10000)

        live.restart()                                  # новый процесс, тот же data_dir
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#cu-stop-panel[data-state='stopped']", timeout=20000)
        assert live.svc._computer_state.stop.is_set()
        browser.close()
