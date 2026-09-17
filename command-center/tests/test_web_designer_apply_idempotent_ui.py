"""Astra/Codex owner-machine observation (2026-09-08, source SHA unattested):

    save heading SAVED → select → inspector text EDITED → Apply → editor and
    preview show EDITED, inspector resets to SAVED → second Apply reverts to SAVED.

Reproduced here on the closure HEAD in a real Chromium against the live
server (the inspector re-rendered from the STALE element description after
the frame reload), and pinned fixed: after an edit the frame re-describes the
selected element, and a second Apply is idempotent.
"""
from __future__ import annotations

import json
import time

import pytest

from .browser_support import click_in_preview, chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

SAVED = "ASTRA_WEB_SAVED_20260908"
EDITED = "ASTRA_WEB_EDITED_20260908"


def _code(page, pid: int) -> str:
    return page.evaluate("""async (pid) => {
      const r = await fetch('/api/web-designer/projects/' + pid, {credentials: 'include'});
      return (await r.json()).code || '';
    }""", pid)


def _wait_code(page, pid, predicate, timeout=15.0, console=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code = _code(page, pid)
        if predicate(code):
            return code
        time.sleep(0.25)
    # Трасса для BL-063: «код не изменился» само по себе ничего не говорит.
    # Что выделено, открыт ли диалог подтверждения (правка body/html с
    # потомками упирается в него без единого запроса), какие тосты видны и
    # какая версия у сервера — всё это отличает «правка не дошла» от «правка
    # ушла не туда» и от «правка отвергнута».
    trace = page.evaluate("""() => ({
      elinfo: [...document.querySelectorAll('div.bd-elinfo')].map(n => n.textContent),
      dialog: [...document.querySelectorAll('.modal[role=dialog] h2')].map(n => n.textContent),
      toasts: [...document.querySelectorAll('.toast .toast-msg')].map(n => n.textContent),
      textInput: (() => { const row = [...document.querySelectorAll('div.bd-row')].find(r => r.textContent.includes('Текст'));
                          const i = row && row.querySelector('input[type=text]'); return i ? i.value : null; })(),
    })""")
    meta = page.evaluate("""async (pid) => {
      const r = await fetch('/api/web-designer/projects/' + pid, {credentials: 'include'});
      const j = await r.json(); return {version: j.meta && j.meta.version, status: r.status};
    }""", pid)
    # §6 (17.09): console and a screenshot travel with the refusal too.
    import os
    from pathlib import Path
    root = Path(os.environ.get("BOSSMAN_EDITOR_EVIDENCE_DIR") or Path.cwd() / ".bossman-state" / "web-designer")
    try:
        root.mkdir(parents=True, exist_ok=True)
        shot = root / f"code-did-not-settle-{pid}-{int(time.time())}.png"
        page.screenshot(path=str(shot), full_page=True)
        shot = str(shot)
    except Exception as exc:  # noqa: BLE001 — evidence, not verdict
        shot = f"screenshot failed: {type(exc).__name__}"
    raise AssertionError(f"code did not settle: {_code(page, pid)[:300]!r}; trace={trace}; server={meta}; "
                         f"console={(console or [])[-10:]!r}; screenshot={shot}")


def _apply_text(page, value: str):
    box = page.locator("input[type=text]").filter(has_not_text="x").first
    # the "Текст" input is the first text input of the inspector row
    row = page.locator("div.bd-row", has_text="Текст").first
    inp = row.locator("input[type=text]").first
    inp.fill(value)
    row.get_by_role("button", name="Применить").click()


def test_apply_twice_keeps_the_edit(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            console: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            _login(page, live)
            pid = page.evaluate("""async (saved) => {
              const csrf = localStorage.getItem('bcc.csrf') || '';
              const h = {'Content-Type': 'application/json', 'X-BCC-CSRF': csrf};
              const r = await fetch('/api/web-designer/projects', {method: 'POST', headers: h,
                body: JSON.stringify({name: 'ASTRA_UI_WEB', prompt: '', template: 'blank'})});
              const pid = (await r.json()).meta.id;
              await fetch('/api/web-designer/projects/' + pid + '/code', {method: 'PUT', headers: h,
                body: JSON.stringify({html: '<!doctype html><html><body><h1>' + saved + '</h1><p>tail</p></body></html>', note: 'seed'})});
              return pid;
            }""", SAVED)
            page.goto(live.url + "/#/web_designer")
            page.wait_for_selector("iframe.bd-frame", timeout=15000)
            page.frame_locator("iframe.bd-frame").locator("h1").first.wait_for(timeout=15000)

            # select → the inspector shows the saved text
            click_in_preview(page, "h1")
            row = page.locator("div.bd-row", has_text="Текст").first
            row.wait_for(timeout=10000)
            inp = row.locator("input[type=text]").first
            page.wait_for_function("(el) => el.value.length > 0", arg=inp.element_handle(), timeout=10000)
            assert inp.input_value() == SAVED

            # edit → Apply
            inp.fill(EDITED)
            row.get_by_role("button", name="Применить").click()
            _wait_code(page, pid, lambda c: EDITED in c and SAVED not in c, console=console)

            # the inspector must now describe the APPLIED element, not the saved one
            row = page.locator("div.bd-row", has_text="Текст").first
            inp = row.locator("input[type=text]").first
            page.wait_for_function("(el) => el.value === arguments[0]" if False else
                                   "([el, want]) => el.value === want",
                                   arg=[inp.element_handle(), EDITED], timeout=10000)

            # Apply again without touching anything: idempotent
            row.get_by_role("button", name="Применить").click()
            time.sleep(1.5)
            code = _wait_code(page, pid, lambda c: EDITED in c)
            assert SAVED not in code, code[:300]
            assert page.frame_locator("iframe.bd-frame").locator("h1").first.inner_text() == EDITED

            # reload the page: the saved code survives and reselection reads it
            page.goto(live.url + "/#/web_designer")
            page.wait_for_selector("iframe.bd-frame", timeout=15000)
            page.frame_locator("iframe.bd-frame").locator("h1").first.wait_for(timeout=15000)
            assert page.frame_locator("iframe.bd-frame").locator("h1").first.inner_text() == EDITED
            click_in_preview(page, "h1")
            row = page.locator("div.bd-row", has_text="Текст").first
            row.wait_for(timeout=10000)
            inp = row.locator("input[type=text]").first
            page.wait_for_function("([el, want]) => el.value === want",
                                   arg=[inp.element_handle(), EDITED], timeout=10000)
            assert errors == [], errors
        finally:
            browser.close()
