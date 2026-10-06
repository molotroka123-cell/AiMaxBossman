"""End-to-end through the REAL Bossman interface (Chromium -> Bossman page -> /api/poker-vision -> poker-vision service).

Nothing here calls the vision code directly: a person-equivalent clicks the page. Expected values come from DOM truth stored
next to the sample frames (never from the pipeline). Set PV_EVIDENCE_DIR to keep page screenshots."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "poker-vision"
SAMPLE = APP_DIR / "evidence" / "sample_frames"
pytest.importorskip("cv2"); pytest.importorskip("uvicorn")
pytestmark = [pytest.mark.timeout(240), pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
              pytest.mark.skipif(not SAMPLE.exists(), reason="sample frames missing")]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


@pytest.fixture
def pv(tmp_path, monkeypatch):
    import uvicorn
    sys.path.insert(0, str(APP_DIR))
    from pokervision.api import create_app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "pv"), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close(); break
        except OSError:
            time.sleep(0.05)
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{port}")
    yield port
    server.should_exit = True; t.join(5)


def _shot(page, name):
    d = os.environ.get("PV_EVIDENCE_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / name), full_page=True)


def test_replay_in_the_bossman_page_reads_fields_shows_overlay_history_and_explains_unknown(live, pv):  # noqa: F811
    from playwright.sync_api import sync_playwright
    expected = json.loads((SAMPLE / "expected_last_frame.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-mode", timeout=20000)
            page.select_option("#pv-mode", "replay")
            page.fill("#pv-target", str(SAMPLE))
            page.click("text=Старт")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('сессия завершена')", timeout=60000)
            page.wait_for_selector("tr[data-field='pot']", timeout=15000)
            row = lambda f: page.inner_text(f"tr[data-field='{f}']")
            assert expected["pot"] in row("pot")
            assert expected["hero_cards"][0] in row("hero_cards[0]") and expected["hero_cards"][1] in row("hero_cards[1]")
            for i, c in enumerate(expected["board"]):
                assert c in row(f"board[{i}]")
            # uncertainty is explained in words (buttons/seats are not readable on this sample)
            assert page.locator("#pv-unc li").count() >= 1
            assert page.inner_text("#pv-unc li") != ""
            # overlay image really loaded from the backend
            page.wait_for_function("document.getElementById('pv-overlay').naturalWidth > 100", timeout=15000)
            assert page.locator("#pv-history details").count() >= 1
            _shot(page, "bossman-poker-vision-replay.png")
        finally:
            browser.close()
    assert not errors, errors


def test_stop_button_halts_a_running_session_in_the_page(live, pv, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright
    import cv2, numpy as np
    d = tmp_path / "many"; d.mkdir()
    for i in range(500):
        cv2.imwrite(str(d / f"{i:04d}.png"), np.full((900, 520, 3), 20, np.uint8))
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-mode", timeout=20000)
            page.fill("#pv-target", str(d))
            # slow it down through the API so STOP is pressed mid-run (the page has no speed control)
            import httpx
            httpx.post(f"http://127.0.0.1:{pv}/api/v1/session", json={"mode": "replay", "adapter": "poker_train", "path": str(d), "interval_s": 0.05}, timeout=10, trust_env=False)
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('идёт сессия')", timeout=20000)
            page.click("button:has-text('STOP')")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('stop нажат')", timeout=20000)
            frames_after_stop = httpx.get(f"http://127.0.0.1:{pv}/api/v1/status", timeout=10, trust_env=False).json()["frames"]
            time.sleep(0.4)
            assert httpx.get(f"http://127.0.0.1:{pv}/api/v1/status", timeout=10, trust_env=False).json()["frames"] == frames_after_stop < 500
            _shot(page, "bossman-poker-vision-stop.png")
        finally:
            browser.close()


def test_trainer_mode_with_a_non_loopback_url_is_refused_in_the_page(live, pv):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-mode", timeout=20000)
            page.select_option("#pv-mode", "trainer")
            page.fill("#pv-target", "https://www.example.com/")
            page.check("#pv-act")
            page.click("text=Старт")
            page.wait_for_function("document.getElementById('pv-msg').innerText.length > 0", timeout=15000)
            assert "loopback" in page.inner_text("#pv-msg").lower() or "не" in page.inner_text("#pv-msg")
            st = page.evaluate("fetch('/api/poker-vision/status').then(r => r.json())")
            assert not (st.get("session") or {}).get("running")
        finally:
            browser.close()
