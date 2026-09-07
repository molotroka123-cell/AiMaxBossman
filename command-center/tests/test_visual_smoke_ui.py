"""Визуальный смоук всех страниц в настоящем Chromium — структурные проверки,
не пиксельные сравнения.

Для каждого маршрута из `window.__bxPages` (тот же источник, что у навигации
и командной палитры) на двух настольных вьюпортах (1440×900 и 1024×768):

* страница отрисована: `#view` имеет дочерние элементы, каркас загрузки снят;
* нет JS-ошибок (`pageerror`) и ошибок консоли, кроме сетевого шума
  недоступных внешних сервисов;
* документ не прокручивается по горизонтали:
  `documentElement.scrollWidth <= clientWidth + 1`;
* основная навигация существует и видима (боковая панель на десктопе);
* настольный док стоит в потоке ПОД содержимым, а не поверх него: ни один
  пиксель `#view` не лежит под доком (регресс в обе стороны: backdrop-filter
  на .shell делал её containing block для position: fixed — док уезжал на
  y ≈ 8000 и закрывал последнюю панель; fixed-док, наоборот, ложился на превью
  веб-дизайнера при 1280×720 и перехватывал клики).

Порог «крупные элементы не уходят за правый край»: любая кнопка/поле внутри
`#view`, чей правый край дальше ширины окна, — падение.
"""
from __future__ import annotations

import json
import re

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(300),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

VIEWPORTS = ((1440, 900), (1024, 768))
NETWORK_NOISE = re.compile(r"net::ERR_|Failed to load resource|the server responded with a status of (404|501|503)", re.I)

JS_STRUCTURE = """() => {
  const de = document.documentElement;
  const view = document.getElementById('view');
  const visible = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const nav = document.getElementById('nav');
  const dock = document.getElementById('desktop-dock');
  const dockRect = dock ? dock.getBoundingClientRect() : null;
  const offRight = [];
  for (const el of view.querySelectorAll('button, input, select, textarea')) {
    const r = el.getBoundingClientRect();
    if (r.width && r.height && r.right > de.clientWidth + 1) offRight.push((el.id || el.className || el.tagName) + '@' + Math.round(r.right));
  }
  return {
    scrollWidth: de.scrollWidth, clientWidth: de.clientWidth,
    children: view.childElementCount, skeleton: !!view.querySelector('.skeleton'),
    navVisible: visible(nav) && nav.querySelectorAll('.nav-item').length > 0,
    dockVisible: visible(dock),
    dockBelowView: dockRect ? (dockRect.top >= view.getBoundingClientRect().bottom - 1) : null,
    offRight: offRight.slice(0, 6),
  };
}"""


def _open_route(page, live, route):  # noqa: F811
    page.goto(f"{live.url}/#/{route['id']}", wait_until="domcontentloaded")
    page.wait_for_function("document.getElementById('page-title').textContent === " + json.dumps(route["title"]), timeout=15000)
    page.wait_for_function("!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0", timeout=20000)
    page.wait_for_timeout(200)


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=[f"{w}x{h}" for w, h in VIEWPORTS])
def test_every_route_is_structurally_sound(live, width, height):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: errors.append(f"[{page.url}] {e}"))
            page.on("console", lambda m: errors.append(f"[{page.url}] {m.text}")
                    if m.type == "error" and not NETWORK_NOISE.search(m.text) else None)
            _login(page, live)
            routes = page.evaluate("window.__bxPages")
            assert len(routes) >= 25, routes

            for route in routes:
                _open_route(page, live, route)
                s = page.evaluate(JS_STRUCTURE)
                rid = route["id"]
                if s["children"] <= 0 or s["skeleton"]:
                    failures.append(f"{rid}@{width}: #view пуст или каркас загрузки не снят")
                if s["scrollWidth"] > s["clientWidth"] + 1:
                    failures.append(f"{rid}@{width}: горизонтальная прокрутка документа {s['scrollWidth']} > {s['clientWidth']}")
                if not s["navVisible"]:
                    failures.append(f"{rid}@{width}: нет основной навигации (#nav .nav-item)")
                if not s["dockVisible"] or s["dockBelowView"] is False:
                    failures.append(f"{rid}@{width}: настольный док отсутствует или лежит поверх #view")
                if s["offRight"]:
                    failures.append(f"{rid}@{width}: элементы за правым краем {s['offRight']}")
        finally:
            browser.close()

    assert not errors, "\n".join(errors)
    assert not failures, "\n".join(failures)


def test_dock_never_covers_content_and_is_reachable(live):  # noqa: F811
    """Отрицательный контроль: на самой длинной странице (Навыки) док не лежит
    ни на одном элементе #view до прокрутки, а после прокрутки в конец
    страницы он целиком виден в окне — то есть его нельзя потерять и он не
    перехватывает клики по содержимому."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            _login(page, live)
            routes = {r["id"]: r for r in page.evaluate("window.__bxPages")}
            _open_route(page, live, routes["skills"])
            probe = """() => {
              const d = document.getElementById('desktop-dock').getBoundingClientRect();
              const v = document.getElementById('view').getBoundingClientRect();
              const cx = d.left + d.width / 2, cy = d.top + d.height / 2;
              const hit = (cy >= 0 && cy <= innerHeight) ? document.elementFromPoint(cx, cy) : null;
              return { dockTop: d.top, dockBottom: d.bottom, viewBottom: v.bottom, inner: innerHeight,
                       hitInDock: hit ? !!hit.closest('#desktop-dock') : null };
            }"""
            before = page.evaluate(probe)
            assert before["dockTop"] >= before["viewBottom"] - 1, before
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(150)
            assert page.evaluate("window.scrollY") > 200, "страница «Навыки» должна быть длиннее 768px — иначе проверка ничего не проверяет"
            after = page.evaluate(probe)
            assert 0 <= after["dockTop"] and after["dockBottom"] <= after["inner"] + 1, after
            assert after["hitInDock"] is True, after
            assert after["dockTop"] >= after["viewBottom"] - 1, after
        finally:
            browser.close()
