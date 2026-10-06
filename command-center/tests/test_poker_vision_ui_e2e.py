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


def _code(body):
    d = body.get('detail') if isinstance(body, dict) else None
    e = body.get('error') if isinstance(body, dict) else None
    return (d or {}).get('code') if isinstance(d, dict) else (e or {}).get('code')


def _pick(page, source_id, mode="observe"):
    page.click("text=Выбрать источник")
    page.wait_for_selector(f"button[data-source='{source_id}']", timeout=15000)
    page.select_option("#pv-pick-mode", mode)
    page.click(f"button[data-source='{source_id}']")


def test_replay_source_shows_live_stream_overlay_fields_history_and_explains_unknown(live, pv):  # noqa: F811
    from playwright.sync_api import sync_playwright
    expected = json.loads((SAMPLE / "expected_last_frame.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1300})
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-stage", timeout=20000)
            # the picker shows every capturable surface with a preview and what is PROVEN for it
            page.click("text=Выбрать источник")
            page.wait_for_selector("button[data-source='replay:sample'] img", timeout=15000)
            assert "UNVERIFIED" in page.inner_text("body") or "PASS" in page.inner_text("body")
            page.keyboard.press("Escape")
            _pick(page, "replay:sample", "observe")
            page.wait_for_function("document.querySelector('#pv-status')?.innerText.toLowerCase().includes('сессия завершена')", timeout=60000)
            page.wait_for_selector("tr[data-field='pot']", timeout=15000)
            row = lambda f: page.inner_text(f"tr[data-field='{f}']")
            assert expected["pot"] in row("pot")
            assert expected["hero_cards"][0] in row("hero_cards[0]") and expected["hero_cards"][1] in row("hero_cards[1]")
            for i, c in enumerate(expected["board"]):
                assert c in row(f"board[{i}]")
            assert page.locator("#pv-unc li").count() >= 1 and page.inner_text("#pv-unc li") != ""
            # the stream is a real image from the capture backend and the overlay draws recognised boxes on top of it
            page.wait_for_function("document.getElementById('pv-stream').naturalWidth > 100", timeout=15000)
            page.wait_for_function("Number(document.getElementById('pv-overlay-canvas').dataset.boxes || 0) > 0", timeout=15000)
            assert page.locator("#pv-history details").count() >= 1
            # modes: replay supports observe and coach but NEVER control
            assert page.is_disabled("button[data-mode='control']")
            _shot(page, "bossman-poker-vision-replay.png")
        finally:
            browser.close()
    assert not errors, errors


def test_refusals_through_the_page_api(live, pv):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 900})
            _login(page, live)
            page.goto(f"{live.url}/#/poker-vision", wait_until="domcontentloaded")
            page.wait_for_selector("#pv-stage", timeout=20000)
            call = lambda body: page.evaluate("""async (b) => { const r = await fetch('/api/poker-vision/desk/start', {method:'POST', headers:{'content-type':'application/json','X-BCC-CSRF': localStorage.getItem('bcc.csrf')||''}, body: JSON.stringify(b)}); return [r.status, await r.json()]; }""", body)
            st, body = call({"source": {"kind": "sandbox", "url": "https://www.example.com/"}, "desk_mode": "observe"})
            assert st in (403, 400) and "loopback" in json.dumps(body).lower()
            st, body = call({"source": {"kind": "sandbox", "url": "http://127.0.0.1:3000/"}, "desk_mode": "control"})
            assert st == 403 and _code(body) == "CONTROL_NEEDS_OWNER_CONFIRM"            # no control without the owner's explicit tick
            st, body = call({"source": {"kind": "replay", "path": str(SAMPLE)}, "desk_mode": "control", "confirm_control": True})
            assert st == 403 and _code(body) == "NOT_ALLOWED"                              # a recording cannot be controlled
            st, body = call({"source": {"kind": "window", "hwnd": 1}, "desk_mode": "observe"})
            assert st == 501 and _code(body) == "NOT_RUN"
            st = page.evaluate("fetch('/api/poker-vision/status').then(r => r.json())")
            assert not ((st.get("session") or {}).get("running"))
        finally:
            browser.close()
