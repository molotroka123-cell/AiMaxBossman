"""BL-063, property 1: ONE real click after documented readiness selects the
exact DOM node — not merely some element with the same tag — without any
hidden retry.

``browser_support.click_in_preview`` is the end-to-end helper: it repeats the
click until the inspector shows a row, and since 17 September it names the
wrong element it selected. That recovery is right for owner scenarios, but it
can hide a first-click defect. This module clicks exactly once and judges the
click by three independent witnesses:

* the frame's own ``[data-bd-selected]`` attribute sits on the element whose
  ``id`` is the target's, and on no other element;
* the host inspector prints ``h1#first-click-target`` as the element and as
  its path (the path is unique because the id is);
* a decoy paragraph directly below the target stays unselected;
* «Применить» on that selection changes exactly that node in the saved code
  (the decoy's text is untouched), and the change is still there after a real
  process restart and a fresh preview.

Readiness is documented, not guessed: the picker script has installed itself
(``window.__bdPicker``), the target is laid out with a non-empty box, the
click point lies inside the window at the frame's real scale, and the pointer
is moved onto the target before the press (the Chromium out-of-process-frame
quirk the helper documents: the first event after a fresh layout can be
resolved asynchronously). On failure the assertion carries the point, the
scale, both witnesses, any open dialog, the console and a screenshot path.
Runs in the installed Windows gate too (``tools/acceptance_registry.json``).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from .browser_support import (chromium_available, hover_until_acknowledged, preview_frame,
                              reason as browser_reason, required)
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401
from .test_web_designer_apply_idempotent_ui import _code, _wait_code

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

TARGET_ID = "first-click-target"
DECOY_ID = "first-click-decoy"
SAVED = "FIRST CLICK 20260917"
EDITED = "FIRST CLICK EDITED 20260917"
DECOY_TEXT = "decoy directly below the heading"
HTML = ("<!doctype html><html><body>"
        f"<h1 id=\"{TARGET_ID}\">{SAVED}</h1>"
        f"<p id=\"{DECOY_ID}\">{DECOY_TEXT}</p>"
        "</body></html>")


@pytest.fixture
def live(editor_server):
    return editor_server


def _seed_project(page, live) -> int:
    _login(page, live)
    return page.evaluate("""async (html) => {
      const csrf = localStorage.getItem('bcc.csrf') || '';
      const h = {'Content-Type': 'application/json', 'X-BCC-CSRF': csrf};
      const r = await fetch('/api/web-designer/projects', {method: 'POST', headers: h, credentials: 'include',
        body: JSON.stringify({name: 'FIRST CLICK 20260917', prompt: '', template: 'blank'})});
      const pid = (await r.json()).meta.id;
      const put = await fetch('/api/web-designer/projects/' + pid + '/code', {method: 'PUT', headers: h,
        credentials: 'include', body: JSON.stringify({html, note: 'seed'})});
      if (!put.ok) throw new Error(await put.text());
      return pid;
    }""", HTML)


def _witnesses(page) -> dict:
    guest = preview_frame(page)
    return {
        "frame_selected": guest.evaluate(
            "() => [...document.querySelectorAll('[data-bd-selected]')].map(e => e.tagName.toLowerCase() + '#' + e.id)"),
        "elinfo": page.evaluate("() => [...document.querySelectorAll('div.bd-elinfo')].map(n => n.textContent)"),
        "dialog": page.evaluate("() => [...document.querySelectorAll('.modal[role=dialog] h2')].map(n => n.textContent)"),
    }


def _screenshot(page, name: str) -> str:
    root = Path(os.environ.get("BOSSMAN_EDITOR_EVIDENCE_DIR") or Path.cwd() / ".bossman-state" / "first-click")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:  # noqa: BLE001 — the screenshot is evidence, not the verdict
        return f"screenshot failed: {type(exc).__name__}"
    return str(path)


def _one_click(page, zoom: str | None, console: list[str]) -> dict:
    """Documented readiness, then exactly one press. Returns the point it used."""
    guest = preview_frame(page)
    guest.wait_for_selector(f"#{TARGET_ID}", timeout=15000)
    if zoom is not None:
        page.get_by_label("Масштаб превью").select_option(value=zoom)
    guest.wait_for_function("() => window.__bdPicker === true", timeout=15000)
    box = guest.evaluate(
        """id => { const el = document.getElementById(id); el.scrollIntoView({block: 'center'});
                  const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                  w: r.width, h: r.height}; }""", TARGET_ID)
    assert box["w"] > 0 and box["h"] > 0, f"the target has no box in the frame: {box}"
    page.evaluate("() => document.querySelector('iframe.bd-frame').scrollIntoView({block: 'center'})")
    spot = page.evaluate(
        """box => { const f = document.querySelector('iframe.bd-frame'); const r = f.getBoundingClientRect();
                   const scale = f.offsetWidth ? r.width / f.offsetWidth : 1;
                   return {x: r.x + box.x * scale, y: r.y + box.y * scale, scale,
                           vw: window.innerWidth, vh: window.innerHeight}; }""", box)
    assert 0 <= spot["x"] <= spot["vw"] and 0 <= spot["y"] <= spot["vh"], f"click point outside the window: {spot}"
    if zoom is not None:
        assert abs(spot["scale"] - float(zoom)) < 0.02, f"frame scale {spot['scale']} is not the selected zoom {zoom}"
    # Readiness is the frame's own word, not a pause: the picker marks the
    # hovered element with data-bd-hover as soon as pointer events reach it.
    # Measured 17.09 (9 runs, zoom 50 %): geometry, picker and point were
    # identical in every run, yet 1 first click in 9 was lost with a single
    # mouse.move + 150 ms — Chromium routes the first events after a frame
    # transform change to the parent until its hit-test data catches up. An
    # owner keeps moving the mouse until the outline appears; so does this.
    hovered = hover_until_acknowledged(page, guest, spot, f"#{TARGET_ID}")
    spot["hover_ms"] = hovered
    assert hovered is not None, f"the frame never acknowledged the pointer over the target: {spot}"
    page.mouse.click(spot["x"], spot["y"])          # the one and only click
    return spot


def _assert_exact_node(page, spot: dict, console: list[str], name: str) -> None:
    """Both witnesses within one budget; the click is never repeated.

    The frame marks the node synchronously in its click handler and only then
    posts 'select' to the host, whose inspector renders on the next task. Run
    112 on windows-latest: the frame mark was there in 94–171 ms, the host's
    two lines landed a few ticks later than the first poll — reading the
    inspector at the instant the mark appeared reported an empty host and
    called a correct first click a failure. Wait for both, then judge.
    """
    deadline = time.monotonic() + 2.0
    seen = _witnesses(page)
    while time.monotonic() < deadline:
        seen = _witnesses(page)
        if seen["frame_selected"] and len(seen["elinfo"]) >= 2:
            break
        page.wait_for_timeout(50)
    expected = f"h1#{TARGET_ID}"
    ok = (seen["frame_selected"] == [expected]
          and seen["elinfo"][:2] == [expected, expected]
          and seen["dialog"] == [])
    if not ok:
        raise AssertionError(
            f"one click did not select the exact node: expected {expected!r}; frame marks {seen['frame_selected']!r}, "
            f"inspector shows {seen['elinfo']!r}, dialogs {seen['dialog']!r}; point {spot}; "
            f"console {console[-10:]!r}; screenshot {_screenshot(page, name)}")


@pytest.mark.parametrize("zoom", [None, "0.5"])
def test_one_click_selects_the_exact_node_not_its_neighbour(live, zoom):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            console: list[str] = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            pid = _seed_project(page, live)
            page.goto(live.url + "/#/web_designer")
            page.wait_for_selector("iframe.bd-frame", timeout=15000)
            spot = _one_click(page, zoom, console)
            _assert_exact_node(page, spot, console, f"first-click-{zoom or 'fit'}")
            # The decoy is a separate node: the id-based witnesses cannot
            # mistake it for the target, and the click must not have touched it.
            guest = preview_frame(page)
            assert guest.evaluate(
                "id => document.getElementById(id).hasAttribute('data-bd-selected')", DECOY_ID) is False

            # Apply on that one selection changes exactly that node in the saved code.
            row = page.locator("div.bd-row", has_text="Текст").first
            row.wait_for(timeout=10000)
            field = row.locator("input[type=text]").first
            page.wait_for_function("([el, want]) => el.value === want", arg=[field.element_handle(), SAVED],
                                   timeout=10000)
            field.fill(EDITED)
            row.get_by_role("button", name="Применить").click()
            code = _wait_code(page, pid, lambda c: EDITED in c, console=console)
            assert f'id="{TARGET_ID}">{EDITED}</h1>' in code, code[:300]
            assert SAVED not in code and DECOY_TEXT in code, code[:300]

            # A real process restart keeps the change; the fresh preview shows it.
            live.restart()
            page.reload()
            page.locator("iframe.bd-frame").wait_for(timeout=15000)
            expect(page.frame_locator("iframe.bd-frame").locator(f"#{TARGET_ID}")).to_have_text(EDITED)
            expect(page.frame_locator("iframe.bd-frame").locator(f"#{DECOY_ID}")).to_have_text(DECOY_TEXT)
            persisted = _code(page, pid)
            assert f'id="{TARGET_ID}">{EDITED}</h1>' in persisted and DECOY_TEXT in persisted
            assert not [line for line in console if line.startswith("pageerror")], console
        finally:
            browser.close()
