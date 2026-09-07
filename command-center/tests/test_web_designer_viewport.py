"""Viewport JS contracts, plus optional real-browser geometry and persistence.

The Node tests execute the production module with real JSON/localStorage-shaped
storage. Browser tests require installed Chromium; a skip is not GUI evidence.
"""
from pathlib import Path
import os
import shutil
import subprocess
import time

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401


def preview_frame(page):
    """Настоящий дочерний фрейм превью (FrameLocator не умеет wait_for_function)."""
    return page.wait_for_selector("iframe.bd-frame").content_frame()


def wait_in_frame(page, expression, timeout=10000):
    """Ждём пересчёта вёрстки ВНУТРИ превью.

    Родитель ставит iframe.style.width синхронно, но собственный вид дочернего
    документа (window.innerWidth, медиазапросы) пересчитывается в его цикле
    кадров. Читать сразу после смены размера — значит читать прошлое состояние и
    получать красный фон там, где через кадр будет синий.
    """
    preview_frame(page).wait_for_function(expression, timeout=timeout)


def click_in_scaled_frame(page, selector, index=0, timeout=10000):
    """Клик по НАСТОЯЩЕЙ экранной точке элемента внутри масштабированного кадра.

    Две поправки к наивному `frame_locator(...).click()`:

    1. Playwright берёт прямоугольник элемента из системы координат самого кадра
       и не учитывает `transform: scale()` на теге <iframe>, поэтому его click
       промахивается на любом масштабе, кроме 100%. Считаем точку сами: origin
       трансформации — top left, значит экранная точка = угол кадра + внутренняя
       координата × масштаб.
    2. Пикер внутри превью включается не сразу: кадр шлёт панели 'ready', панель
       отвечает 'pick', и только тогда клики перехватываются. Наблюдаемый признак
       готовности — data-bd-hover, который ставится ТОЛЬКО при включённом пикере.
       Ждём его под курсором, а не спим и не кликаем вслепую.
    """
    deadline = time.monotonic() + timeout / 1000
    probe = "([sel, i]) => document.querySelectorAll(sel)[i].hasAttribute('data-bd-hover')"
    while True:
        handle = page.wait_for_selector("iframe.bd-frame")
        child = handle.content_frame()
        child.wait_for_load_state("load")          # шрифты и картинки ещё двигают вёрстку
        inner = child.evaluate(
            """([sel, i]) => {
                 const r = document.querySelectorAll(sel)[i].getBoundingClientRect();
                 return {x: r.left + r.width / 2, y: r.top + r.height / 2};
               }""", [selector, index])
        host = handle.evaluate(
            """el => {
                 const r = el.getBoundingClientRect();
                 return {left: r.left, top: r.top, scale: r.width / el.offsetWidth};
               }""")
        x = host["left"] + inner["x"] * host["scale"]
        y = host["top"] + inner["y"] * host["scale"]
        page.mouse.move(x, y - 1)
        page.mouse.move(x, y)
        try:
            child.wait_for_function(probe, arg=[selector, index], timeout=1000)
        except Exception:
            # Либо пикер ещё не включён панелью, либо вёрстка уехала после
            # измерения. И то и другое лечится повторным замером, а не сном.
            if time.monotonic() >= deadline:
                raise
            continue
        page.mouse.click(x, y)
        return


def test_viewport_module_serialization_and_geometry():
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: production JS viewport contracts were not executed")
    result = subprocess.run([node, "--test", str(Path(__file__).with_suffix('.mjs'))],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.timeout(180)
@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
def test_viewport_toolbar_changes_actual_iframe_geometry_without_editing_project(live):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            _login(page, live)
            initial = page.evaluate("""async () => {
              const headers = {'Content-Type':'application/json', 'X-BCC-CSRF':localStorage.getItem('bcc.csrf') || ''};
              const created = await (await fetch('/api/web-designer/projects', {method:'POST', headers,
                body:JSON.stringify({name:'Viewport fixture', template:'blank'})})).json();
              const id = Number(created.meta.id);
              const saved = await fetch(`/api/web-designer/projects/${id}/code`, {method:'PUT', headers,
                body:JSON.stringify({base_version:created.meta.version,
                  html:'<!doctype html><html><head><style>body{background:red}@media(max-width:500px){body{background:blue}}</style></head><body><h1>Responsive fixture</h1></body></html>'})});
              if (!saved.ok) throw new Error(`Fixture save failed: ${saved.status}`);
              return await (await fetch(`/api/web-designer/projects/${id}`)).json();
            }""")
            pid = int(initial["meta"]["id"])
            page.goto(live.url + f"/#/web_designer?project={pid}")
            page.wait_for_selector("iframe.bd-frame")
            frame = page.frame_locator("iframe.bd-frame")
            frame.locator("h1").wait_for()
            page.get_by_label("Размер экрана превью", exact=True).select_option("mobile")
            page.wait_for_function("() => document.querySelector('iframe.bd-frame').style.width === '390px'")
            expect(frame.locator("body")).to_have_css("background-color", "rgb(0, 0, 255)")
            assert frame.locator("body").evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(0, 0, 255)"
            wait_in_frame(page, "() => window.innerWidth === 390")
            assert frame.locator("body").evaluate("() => window.innerWidth") == 390
            page.get_by_role("button", name="Повернуть", exact=True).click()
            wait_in_frame(page, "() => window.innerWidth === 844 && window.innerHeight === 390")
            assert frame.locator("body").evaluate("() => window.innerWidth") == 844
            assert frame.locator("body").evaluate("() => window.innerHeight") == 390
            page.get_by_label("Масштаб превью", exact=True).select_option("0.5")
            page.wait_for_function(
                "() => Math.round(document.querySelector('iframe.bd-frame').getBoundingClientRect().width) === 422")
            geometry = page.locator("iframe.bd-frame").evaluate("el => ({w:el.getBoundingClientRect().width,h:el.getBoundingClientRect().height})")
            assert geometry == {"w": 422, "h": 195}
            # Scaling must preserve the sandbox picker bridge to the inspector.
            click_in_scaled_frame(page, "h1")
            page.wait_for_function(
                "() => document.querySelector('.bd-elinfo')?.textContent === 'h1'")
            page.get_by_label("Высота превью", exact=True).fill("4096")
            page.get_by_role("button", name="Применить размер", exact=True).click()
            page.get_by_label("Масштаб превью", exact=True).select_option("width")
            geometry = page.locator("iframe.bd-frame").evaluate("""el => {
              const stage = document.querySelector('.bd-viewport-stage');
              const rect = el.getBoundingClientRect();
              return {w:rect.width,h:rect.height,available:stage.clientWidth-24,
                scrolls:stage.scrollHeight > stage.clientHeight};
            }""")
            assert geometry["w"] == pytest.approx(min(844, geometry["available"]), abs=1)
            assert geometry["h"] / geometry["w"] == pytest.approx(4096 / 844)
            assert geometry["scrolls"]
            wait_in_frame(page, "() => window.innerWidth === 844")
            assert frame.locator("body").evaluate("() => window.innerWidth") == 844
            page.get_by_label("Ширина превью", exact=True).fill("0")
            page.get_by_role("button", name="Применить размер", exact=True).click()
            assert frame.locator("body").evaluate("() => window.innerWidth") == 844
            page.reload()
            page.wait_for_selector("iframe.bd-frame")
            page.wait_for_function("() => document.querySelector('iframe.bd-frame').style.width === '844px'")
            assert page.get_by_label("Масштаб превью", exact=True).input_value() == "width"
            wait_in_frame(page, "() => window.innerHeight === 4096")
            assert frame.locator("body").evaluate("() => window.innerHeight") == 4096
            final = page.evaluate("async id => (await fetch(`/api/web-designer/projects/${id}`)).json()", pid)
            assert final == initial  # no code, metadata, or version-history mutation
            assert page.get_attribute("iframe.bd-frame", "sandbox") == "allow-scripts"
            assert errors == []
        finally:
            browser.close()
