"""Viewport JS contracts, plus optional real-browser geometry and persistence.

The Node tests execute the production module with real JSON/localStorage-shaped
storage. Browser tests require installed Chromium; a skip is not GUI evidence.
"""
from pathlib import Path
import os
import shutil
import subprocess

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401


def test_viewport_module_serialization_and_geometry():
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: production JS viewport contracts were not executed")
    result = subprocess.run([node, "--test", str(Path(__file__).with_suffix('.mjs'))],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _settles(frame, expression, expected, *, timeout: float = 10.0):
    """Значение ВНУТРИ кадра после изменения его размера снаружи.

    Хост меняет `style.width` кадра синхронно, а кадр пересчитывает свой CSS-
    вьюпорт и медиазапросы уже следующим тиком: сравнение сразу после записи
    стиля читает старое состояние кадра и делает тест гонкой, а не проверкой.
    Ожидание не ослабляет проверку — то же самое равенство обязано наступить,
    и невыполнение по-прежнему валит тест.
    """
    import time
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        seen = frame.locator("body").evaluate(expression)
        if seen == expected:
            return seen
        time.sleep(0.1)
    raise AssertionError(f"{expression}: получено {seen!r}, ожидалось {expected!r}")


@pytest.mark.timeout(180)
@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
def test_viewport_toolbar_changes_actual_iframe_geometry_without_editing_project(live):
    from playwright.sync_api import sync_playwright

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
            _settles(frame, "el => getComputedStyle(el).backgroundColor", "rgb(0, 0, 255)")
            _settles(frame, "() => window.innerWidth", 390)
            page.get_by_role("button", name="Повернуть", exact=True).click()
            _settles(frame, "() => window.innerWidth", 844)
            _settles(frame, "() => window.innerHeight", 390)
            page.get_by_label("Масштаб превью", exact=True).select_option("0.5")
            geometry = page.locator("iframe.bd-frame").evaluate("el => ({w:el.getBoundingClientRect().width,h:el.getBoundingClientRect().height})")
            assert geometry == {"w": 422, "h": 195}
            # Scaling must preserve the sandbox picker bridge to the inspector.
            frame.locator("h1").click()
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
            _settles(frame, "() => window.innerWidth", 844)
            page.get_by_label("Ширина превью", exact=True).fill("0")
            page.get_by_role("button", name="Применить размер", exact=True).click()
            _settles(frame, "() => window.innerWidth", 844)
            page.reload()
            page.wait_for_selector("iframe.bd-frame")
            page.wait_for_function("() => document.querySelector('iframe.bd-frame').style.width === '844px'")
            assert page.get_by_label("Масштаб превью", exact=True).input_value() == "width"
            _settles(frame, "() => window.innerHeight", 4096)
            final = page.evaluate("async id => (await fetch(`/api/web-designer/projects/${id}`)).json()", pid)
            assert final == initial  # no code, metadata, or version-history mutation
            assert page.get_attribute("iframe.bd-frame", "sandbox") == "allow-scripts"
            assert errors == []
        finally:
            browser.close()
