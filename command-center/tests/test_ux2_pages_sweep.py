"""UX 2.0 — проверка кнопок и функций всех страниц в реальном Chromium против живого сервера.
Для каждой страницы: рендер без панели «Повторить», 0 ошибок консоли (кроме сетевого шума
недоступных внешних сервисов), у каждой видимой кнопки есть имя (текст/aria-label/title),
нет «кракозябр» (двойная UTF-8-кодировка), а кнопки-открывашки (Новый/Создать/Добавить/Настроить)
реально открывают модальное окно, которое закрывается по Esc."""
from __future__ import annotations

import json
import re

import pytest

from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401
from .browser_support import chromium_available, reason as browser_reason

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

NETWORK_NOISE = re.compile(r"net::ERR_|Failed to load resource|the server responded with a status of (404|501|503)", re.I)
OPENER = re.compile(r"^(Нов(ый|ая|ое)|Создать|Добавить|Настро(ить|йки)|Подключить|Импорт)", re.I)
# «Ð»/«Ñ» (U+00D0/U+00D1) + символ Latin-1/кириллицы = двойная UTF-8-кодировка; в русском UI их не бывает
MOJIBAKE = re.compile("[ÐÑ][-ÿ–-™Ѐ-џ]")

JS_AUDIT = """() => {
  const view = document.getElementById('view');
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const buttons = Array.from(view.querySelectorAll('button')).filter(visible);
  const unnamed = buttons.filter(b => !(b.textContent.trim() || b.getAttribute('aria-label') || b.getAttribute('title')))
    .map(b => b.outerHTML.slice(0, 120));
  const labels = buttons.filter(b => !b.disabled).map(b => b.textContent.trim()).filter(Boolean);
  const retry = Array.from(view.querySelectorAll('button')).some(b => b.textContent.trim() === 'Повторить');
  return { buttons: buttons.length, unnamed, labels, retry, text: view.innerText };
}"""


def test_every_page_renders_and_buttons_work(live, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    report: list[dict] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(f"[{page.url}] {m.text}") if m.type == "error" and not NETWORK_NOISE.search(m.text) else None)
        page.on("pageerror", lambda e: errors.append(f"[{page.url}] {e}"))
        _login(page, live)
        pages = page.evaluate("window.__bxPages")
        assert len(pages) >= 25, pages

        for p in pages:
            page.goto(f"{live.url}/#/{p['id']}", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('page-title').textContent === " + json.dumps(p["title"]), timeout=15000)
            page.wait_for_function("!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0", timeout=20000)
            page.wait_for_timeout(250)
            audit = page.evaluate(JS_AUDIT)
            row = {"id": p["id"], "title": p["title"], "buttons": audit["buttons"], "unnamed": audit["unnamed"],
                   "retry_panel": audit["retry"], "mojibake": bool(MOJIBAKE.search(audit["text"])), "opened_modal": []}
            # кнопки-открывашки: должны открыть модалку, Esc — закрыть
            for label in [l for l in audit["labels"] if OPENER.search(l)][:3]:
                btn = page.locator("#view button", has_text=re.compile("^" + re.escape(label) + "$")).first
                if not btn.is_visible():
                    continue
                hash_before = page.evaluate("location.hash")
                btn.click()
                try:
                    # допустимые исходы: открылась модалка (закрываем Esc) или страница перешла на другой раздел
                    page.wait_for_function(
                        "([h]) => !!document.querySelector('#modal-root .modal') || location.hash !== h",
                        arg=[hash_before], timeout=5000)
                    if page.evaluate("location.hash") != hash_before:
                        row["opened_modal"].append({"label": label, "ok": True, "nav": page.evaluate("location.hash")})
                        page.goto(f"{live.url}/#/{p['id']}", wait_until="domcontentloaded")
                        page.wait_for_function("!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0", timeout=20000)
                    else:
                        page.keyboard.press("Escape")
                        page.wait_for_selector("#modal-root .modal", state="detached", timeout=5000)
                        row["opened_modal"].append({"label": label, "ok": True})
                except Exception as exc:  # noqa: BLE001
                    row["opened_modal"].append({"label": label, "ok": False, "error": str(exc).splitlines()[0]})
                    page.keyboard.press("Escape")
            report.append(row)
        browser.close()

    (tmp_path / "ux2_sweep.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    lines = [f"{r['id']:<16} buttons={r['buttons']:<3} modal={','.join(('OK:' if m['ok'] else 'FAIL:') + m['label'] for m in r['opened_modal']) or '-'}" for r in report]
    print("\n" + "\n".join(lines))

    failures = []
    for r in report:
        if r["retry_panel"]:
            failures.append(f"{r['id']}: страница показала «Повторить» (рендер упал)")
        if r["unnamed"]:
            failures.append(f"{r['id']}: кнопки без имени: {r['unnamed']}")
        if r["mojibake"]:
            failures.append(f"{r['id']}: кракозябры в тексте страницы")
        for m in r["opened_modal"]:
            if not m["ok"]:
                failures.append(f"{r['id']}: «{m['label']}» не открыла/не закрыла модалку: {m.get('error')}")
    assert not failures, "\n".join(failures)
    assert errors == [], "\n".join(errors)


def test_every_page_fits_mobile_viewport(live):  # noqa: F811
    """Телефон (390×844): ни одна страница не даёт горизонтальной прокрутки, консоль чистая,
    панель «Процесс работы» на телефоне занимает всю ширину и закрывается."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    overflow: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page.on("console", lambda m: errors.append(f"[{page.url}] {m.text}") if m.type == "error" and not NETWORK_NOISE.search(m.text) else None)
        page.on("pageerror", lambda e: errors.append(f"[{page.url}] {e}"))
        _login(page, live)
        pages = page.evaluate("window.__bxPages")
        for p in pages:
            page.goto(f"{live.url}/#/{p['id']}", wait_until="domcontentloaded")
            page.wait_for_function("!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0", timeout=20000)
            page.wait_for_timeout(150)
            w = page.evaluate("({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth })")
            if w["sw"] > w["cw"] + 1:
                overflow.append(f"{p['id']}: scrollWidth {w['sw']} > viewport {w['cw']}")
        # панель процесса на телефоне: во всю ширину, закрывается кнопкой
        page.click("#think-open")
        page.wait_for_selector("#think-pane:not([hidden])", timeout=5000)
        box = page.locator("#think-pane").bounding_box()
        assert box and box["width"] >= 380, box
        page.click("#think-close")
        page.wait_for_selector("#think-pane[hidden]", state="attached", timeout=5000)
        browser.close()

    assert overflow == [], "\n".join(overflow)
    assert errors == [], "\n".join(errors)
