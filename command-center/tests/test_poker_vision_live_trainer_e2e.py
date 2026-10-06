"""Live run through the Bossman page against the owner's own Poker Train (loopback). Skipped unless POKERTRAIN_URL points at a
running trainer (npm run dev). Acts only in that trainer; verifies the state changed after every click; STOP ends it."""
from __future__ import annotations

import os
import socket
import time
from urllib.parse import urlparse

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_poker_vision_ui_e2e import _shot, pv  # noqa: F401
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

URL = os.environ.get("POKERTRAIN_URL", "")


def _up() -> bool:
    if not URL:
        return False
    u = urlparse(URL)
    try:
        socket.create_connection((u.hostname, u.port or 80), 0.5).close(); return True
    except OSError:
        return False


pytestmark = [pytest.mark.timeout(420), pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
              pytest.mark.skipif(not _up(), reason="set POKERTRAIN_URL to a running Poker Train (loopback)")]


def test_vision_plays_hands_in_the_own_trainer_through_the_bossman_page_and_stop_ends_it(live, pv):  # noqa: F811
    from playwright.sync_api import sync_playwright
    import httpx
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1150})
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-mode", timeout=20000)
            page.select_option("#pv-mode", "trainer")
            page.fill("#pv-target", URL)
            page.check("#pv-act"); page.check("#pv-boot")
            page.click("text=Старт")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('идёт сессия')", timeout=60000)
            deadline = time.time() + 240
            hist = {}
            while time.time() < deadline:
                hist = httpx.get(f"{live.url}/api/poker-vision/history", headers={"Authorization": f"Bearer {live.svc.auth.token}"}, timeout=20, trust_env=False).json() if False else \
                       page.evaluate("fetch('/api/poker-vision/history').then(r => r.json())")
                if len(hist["decisions"]) >= 6:
                    break
                time.sleep(2)
            _shot(page, "bossman-poker-vision-live-trainer.png")
            assert len(hist["decisions"]) >= 6, hist
            assert all(d["clicked"] in ("FOLD", "CHECK", "CALL", "RAISE") for d in hist["decisions"])
            assert sum(1 for d in hist["decisions"] if d.get("verified")) >= 0.8 * len(hist["decisions"])
            assert all("hand" in d and len(d["hand"]) == 2 for d in hist["decisions"])
            page.click("button:has-text('STOP')")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('stop нажат')", timeout=30000)
            n = page.evaluate("fetch('/api/poker-vision/status').then(r => r.json())")["session"]["frames"]
            time.sleep(1.0)
            assert page.evaluate("fetch('/api/poker-vision/status').then(r => r.json())")["session"]["frames"] == n
            _shot(page, "bossman-poker-vision-live-after-stop.png")
        finally:
            browser.close()
