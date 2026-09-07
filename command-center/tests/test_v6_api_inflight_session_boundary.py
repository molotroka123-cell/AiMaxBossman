"""V6 §5: склейка одинаковых GET в api.js обязана знать границу сессии.

Замечено CI (py3.12, bed5e8c9): `api.system()` после `logout()` вернул 200 —
это был промис запроса, начатого ещё под старой сессией. Наблюдение не имеет
права пересекать границу аутентификации. Пара контролей: законная склейка
внутри сессии остаётся, переиспользование через logout — запрещено.
"""
from __future__ import annotations

import asyncio

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _slow_history(live, seconds: float):  # noqa: F811
    async def history(n):
        await asyncio.sleep(seconds)
        return []
    live.svc.metrics.history = history      # /api/system ждёт истории метрик


def test_concurrent_gets_within_one_session_still_coalesce(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    _slow_history(live, 0.8)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.wait_for_timeout(1500)          # стартовые запросы оболочки закончились
            hits = []
            page.on("request", lambda r: hits.append(r.url) if r.url.endswith("/api/system") else None)
            out = page.evaluate("""async () => {
              const {api} = await import('/api.js');
              const [a, b, c] = await Promise.all([api.system(), api.system(), api.system()]);
              return [a.started_at, b.started_at, c.started_at];
            }""")
            assert out[0] == out[1] == out[2]
            assert len(hits) == 1, hits
        finally:
            browser.close()


def test_a_get_started_before_logout_is_not_reused_after_it(live):  # noqa: F811
    from playwright.sync_api import sync_playwright
    _slow_history(live, 1.5)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.wait_for_timeout(2500)
            result = page.evaluate("""async () => {
              const {api} = await import('/api.js');
              const before = api.system();          // летит под старой сессией
              await new Promise((r) => setTimeout(r, 50));
              await api.logout();
              let after;
              try { after = {ok: true, data: await api.system()}; }
              catch (e) { after = {ok: false, status: e.status, isAuth: e.isAuth}; }
              let old;
              try { old = {ok: true}; await before; } catch (e) { old = {ok: false, status: e.status}; }
              return {after, old};
            }""")
            assert result["after"] == {"ok": False, "status": 401, "isAuth": True}, result
        finally:
            browser.close()
