"""V6 §C: ленивый реестр страниц не имеет права расходиться с модулями.

Три вещи проверяются в настоящем Chromium против живого сервера:
1. манифест `pages/index.js` == модуль: каждое статическое поле страницы
   (id, title, icon, nav, section) совпадает с тем, что экспортирует модуль,
   и у модуля нет полей/функций, которых манифест не знает;
2. до первой отрисовки оболочка НЕ тянет код всех страниц (иначе ленивость —
   слова), а переход на невиданную страницу догружает её модуль и рисует её;
3. предзагрузка в простое действительно догружает остальные модули.
"""
from __future__ import annotations

import json

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

STATIC_KEYS = {"id", "title", "icon", "nav", "section"}
FUNCTION_KEYS = {"render", "onEvent"}

FIRST_RENDER_SNAPSHOT = """() => {
  if (!performance.getEntriesByName('bossman:first_page_rendered').length) return false;
  return JSON.stringify(performance.getEntriesByType('resource').map((e) => e.name));
}"""


def test_manifest_matches_every_module(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            page.goto(live.url + "/", wait_until="domcontentloaded")
            rows = page.evaluate("""async () => {
              const { FEATURE_PAGES } = await import('/pages/index.js');
              const out = [];
              for (const p of FEATURE_PAGES) {
                const manifest = {};
                for (const k of Object.keys(p)) if (typeof p[k] !== 'function' && !k.startsWith('__')) manifest[k] = p[k];
                const real = await p.__load();
                const statics = {}, fns = [];
                for (const k of Object.keys(real)) { if (typeof real[k] === 'function') fns.push(k); else statics[k] = real[k]; }
                out.push({ id: p.id, manifest, statics, fns, loaded: p.__loaded,
                           hasRender: typeof p.render === 'function', hasOnEvent: typeof p.onEvent === 'function' });
              }
              return out;
            }""")
        finally:
            browser.close()
    assert len(rows) >= 25, rows
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "дубликаты id в реестре"
    for r in rows:
        assert r["manifest"] == r["statics"], f"{r['id']}: манифест {r['manifest']} != модуль {r['statics']}"
        assert set(r["statics"]) <= STATIC_KEYS, f"{r['id']}: неизвестное статическое поле — обновите lazyPage"
        assert set(r["fns"]) <= FUNCTION_KEYS, f"{r['id']}: модуль экспортирует {r['fns']}, обёртка знает только {sorted(FUNCTION_KEYS)}"
        assert "render" in r["fns"] and r["hasRender"] and r["hasOnEvent"] and r["loaded"]


def test_shell_renders_first_page_without_loading_every_module_and_navigation_loads_on_demand(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("window.__bxPreload = false;")   # замер без предзагрузки
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            snapshot = page.wait_for_function(FIRST_RENDER_SNAPSHOT, timeout=20000).json_value()
            loaded = [u.rsplit("/", 1)[-1] for u in json.loads(snapshot) if "/pages/" in u and u.endswith(".js")]
            page_modules = [u for u in loaded if not u.startswith("_") and u != "index.js"]
            # посадочная страница и её зависимости — да; все 28 модулей — нет
            assert 0 < len(page_modules) <= 6, page_modules
            assert "objectives.js" not in page_modules and "trading_lab.js" not in page_modules

            state = page.evaluate("""async () => {
              const { FEATURE_PAGES } = await import('/pages/index.js');
              const p = FEATURE_PAGES.find((x) => x.id === 'objectives');
              return { loaded: p.__loaded, onEvent: p.onEvent({ kind: 'noop' }, {}) };
            }""")
            assert state == {"loaded": False, "onEvent": False}, state

            page.goto(f"{live.url}/#/objectives", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('page-title').textContent === 'Цели'", timeout=15000)
            page.wait_for_function("!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0", timeout=20000)
            after = page.evaluate("""async () => {
              const { FEATURE_PAGES } = await import('/pages/index.js');
              return FEATURE_PAGES.find((x) => x.id === 'objectives').__loaded;
            }""")
            assert after is True
            assert not errors, errors
        finally:
            browser.close()


def test_idle_preload_loads_the_remaining_modules(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.wait_for_function(FIRST_RENDER_SNAPSHOT, timeout=20000)
            page.wait_for_function("""async () => {
              const { FEATURE_PAGES } = await import('/pages/index.js');
              return FEATURE_PAGES.every((p) => p.__loaded);
            }""", timeout=30000)
        finally:
            browser.close()
