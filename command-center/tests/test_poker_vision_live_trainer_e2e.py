"""Acceptance through the REAL Bossman page: source picker -> live stream with overlay -> validated state -> recommendation (Подсказки)
-> action in the owner's own trainer (Управление, after the owner's explicit tick) -> verified on a new frame -> decision journal -> history;
then window operations and STOP. Needs POKERTRAIN_URL (own trainer, loopback). Screenshots to PV_EVIDENCE_DIR."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401
from .test_poker_vision_ui_e2e import _code, _free_port, _pick, _shot, pv  # noqa: F401

URL = os.environ.get("POKERTRAIN_URL", "http://127.0.0.1:3000/")


def _up() -> bool:
    u = urlparse(URL)
    try:
        socket.create_connection((u.hostname, u.port or 80), 0.5).close(); return True
    except OSError:
        return False


pytestmark = [pytest.mark.timeout(420), pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
              pytest.mark.skipif(not _up(), reason="set POKERTRAIN_URL to a running Poker Train (loopback)")]


def _api(page, path, body=None):
    return page.evaluate("""async ([p, b]) => { const r = await fetch(p, b ? {method:'POST', headers:{'content-type':'application/json','X-BCC-CSRF': localStorage.getItem('bcc.csrf')||''}, body: JSON.stringify(b)} : {}); return r.json(); }""", [path, body])


def test_observe_to_coach_to_control_to_confirmation_to_history_then_window_ops_and_stop(live, pv):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1500})
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-stage", timeout=20000)
            # 1. choose the source in the picker (screen-share style) and watch
            _pick(page, "sandbox:trainer", "coach")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('идёт сессия')", timeout=60000)
            page.wait_for_function("document.getElementById('pv-stream').naturalWidth > 100", timeout=60000)
            # 2. validated state + recommendation with an explanation, options and the uncertainty disclaimer
            page.wait_for_selector("[data-rec-action]", timeout=120000)
            assert "не гарантия" in page.inner_text("#pv-rec")
            page.wait_for_function("Number(document.getElementById('pv-overlay-canvas').dataset.boxes || 0) > 3", timeout=30000)
            _shot(page, "bossman-poker-vision-coach.png")
            # control is refused without the owner's tick, allowed with it
            assert _code(_api(page, "/api/poker-vision/desk/mode", {"mode": "control"})) == "CONTROL_NEEDS_OWNER_CONFIRM"
            page.check("#pv-confirm-control")
            page.click("button[data-mode='control']")
            # 3. actions in the trainer, each confirmed on a new frame, journal with SHA and before/after frames
            # (a halt after an unconfirmed click is the designed behaviour; like the owner, the test reviews it in the page and resumes)
            def verified_rows():
                j_ = _api(page, "/api/poker-vision/journal")
                return [r for r in j_["records"] if r.get("event") == "action" and r.get("verified")]
            deadline = time.time() + 240
            resumed = 0
            while time.time() < deadline and len(verified_rows()) < 2:
                if page.locator("button:has-text('Снять остановку после осмотра')").count():
                    page.click("button:has-text('Снять остановку после осмотра')"); resumed += 1
                time.sleep(1.0)
            assert len(verified_rows()) >= 2, _api(page, "/api/poker-vision/status")["session"]["desk"]
            txt = page.inner_text("#pv-journal")
            assert "подтверждено" in txt and "SHA" in txt
            j = _api(page, "/api/poker-vision/journal")
            acts = [r for r in j["records"] if r.get("event") == "action"]
            assert j["sha"] and all(a["before"] and a["after"] for a in acts if a["verified"]), [(a["label"], a["verified"], a["verify"]) for a in acts]
            assert all(a["backend"]["via"] == "pointer" for a in acts)
            _shot(page, "bossman-poker-vision-control.png")
            hist = _api(page, "/api/poker-vision/history")
            assert hist["hands"] and hist["decisions"]
            # 4. a window on top halts control from the page; nothing is clicked while it stays there
            page.click("text=Перекрыть")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('управление остановлено')", timeout=40000)
            n = _api(page, "/api/poker-vision/status")["session"]["desk"]["executor"]["n_actions"]
            time.sleep(2)
            assert _api(page, "/api/poker-vision/status")["session"]["desk"]["executor"]["n_actions"] == n
            _shot(page, "bossman-poker-vision-occluded-halt.png")
            # 5. pause and STOP
            page.click("button:has-text('Пауза')")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('пауза')", timeout=15000)
            page.click("button:has-text('STOP')")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('stop нажат')", timeout=30000)
            f1 = _api(page, "/api/poker-vision/status")["session"]["frames"]
            time.sleep(1.0)
            assert _api(page, "/api/poker-vision/status")["session"]["frames"] == f1
            _shot(page, "bossman-poker-vision-after-stop.png")
        finally:
            browser.close()
