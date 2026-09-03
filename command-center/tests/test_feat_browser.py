"""Feature 09 — Browser: политика (чистая) + реальный Playwright-прогон + takeover."""
import pytest

from bcc.v2.browser_control import BrowserPolicy


@pytest.fixture(autouse=True)
def _allow_private_browser_targets(monkeypatch):
    """F-010: по умолчанию браузер не ходит на loopback/приватные адреса; эти
    тесты поднимают тестовый HTTP-сервер на 127.0.0.1 — включаем owner-override
    только для них (сама политика проверяется в test_secrem_browser_policy.py)."""
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


# ---------- политика (чистая логика) ----------

def test_policy_denies_payment_wallet():
    pol = BrowserPolicy.from_dict({})
    assert pol.decision("payment") == "deny"
    assert pol.decision("wallet") == "deny"
    assert pol.decision("bank_transfer") == "deny"


def test_policy_auto_navigate_ask_login():
    pol = BrowserPolicy.from_dict({})
    assert pol.decision("navigate", url="http://example.com") == "auto"
    assert pol.decision("click") == "auto"
    assert pol.decision("login") == "ask"
    assert pol.decision("upload") == "ask"


def test_policy_blocked_domain_denies_nav():
    pol = BrowserPolicy.from_dict({"blocked_domains": ["evil.com"]})
    assert pol.decision("navigate", url="http://evil.com/x") == "deny"


# ---------- реальный браузер (Playwright + предустановленный Chromium) ----------

def _has_chromium():
    from .browser_support import chromium_available
    return chromium_available()


@pytest.mark.skipif(not _has_chromium(), reason="Chromium не предустановлен")
async def test_real_browser_flow_and_takeover(env, tmp_path):
    # тестовая страница через локальный http-сервер (file:// не проходит domain-check)
    import functools
    import http.server
    import socketserver
    import threading
    (tmp_path / "p1.html").write_text(
        "<html><body><h1>Первая</h1><input id='q'></body></html>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/p1.html"

    created = await env.client.post("/api/browser/sessions", json={})
    if created.status_code == 503:
        pytest.skip("Playwright недоступен в этом окружении")
    sid = created.json()["session_id"]

    # navigate (auto) → DOM snapshot первичен
    nav = (await env.client.post(f"/api/browser/sessions/{sid}/act",
                                 json={"action": "navigate", "url": url})).json()
    # навигация реально произошла (url указывает на нашу страницу — детерминированно;
    # innerText в headless без layout может быть пуст — это особенность рендера, не бага)
    assert str(port) in nav.get("url", "")

    # type в поле (auto)
    typed = await env.client.post(f"/api/browser/sessions/{sid}/act",
                                  json={"action": "type", "selector": "#q", "text": "привет"})
    assert typed.status_code == 200

    # скриншот сохраняется и отдаётся
    shot = await env.client.get(f"/api/browser/sessions/{sid}/screenshot")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"

    # Human Take Over → действия агента заблокированы (409)
    await env.client.post(f"/api/browser/sessions/{sid}/takeover")
    blocked = await env.client.post(f"/api/browser/sessions/{sid}/act",
                                    json={"action": "click", "selector": "#q"})
    assert blocked.status_code == 409

    # Resume → снова можно
    await env.client.post(f"/api/browser/sessions/{sid}/resume")
    ok = await env.client.post(f"/api/browser/sessions/{sid}/act",
                               json={"action": "snapshot"})
    assert ok.status_code == 200

    # Stop
    await env.client.post(f"/api/browser/sessions/{sid}/stop")
    state = await env.client.get(f"/api/browser/sessions/{sid}/state")
    assert state.status_code == 404       # сессия остановлена
    httpd.shutdown()


async def test_browser_denies_payment_via_api(env):
    """payment запрещён политикой — даже если сессия не стартовала, act проверяет."""
    # без реального браузера act вернёт 404 (сессия не запущена) — проверяем deny на политике
    pol = BrowserPolicy.from_dict({})
    assert pol.decision("purchase") == "deny"


# ---------- отказ запуска сессии: строка не должна зависать в created ----------

@pytest.mark.asyncio
async def test_unexpected_start_failure_marks_session_failed_not_created(env, monkeypatch):
    """P1 из свипа владельца: строка `id=1 status=created` при упавшем старте.

    Эндпоинт ловил только BrowserUnavailable/BrowserPolicyDenied; любой другой
    взрыв Playwright (нет памяти, нет браузера, умер GPU) уходил наружу, а
    сессия навсегда оставалась в `created` — по базе не отличить «создаётся»
    от «взорвалось».
    """
    import sqlalchemy as sa

    from bcc.features import browser as feat
    from bcc.v2.tables import browser_sessions as bs_t

    class _Boom:
        async def start(self, *a, **k):
            raise RuntimeError("playwright: Target page, context or browser has been closed")

    monkeypatch.setattr(feat, "_mgr", lambda svc: _Boom())

    resp = await env.client.post("/api/browser/sessions", json={})
    assert resp.status_code == 500, resp.text
    assert "не удалось запустить сессию браузера" in resp.text

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(bs_t.c.id, bs_t.c.status))).fetchall()
    assert rows, "строка сессии создана"
    assert all(r[1] == "failed" for r in rows), rows      # не осталась в created


@pytest.mark.asyncio
async def test_policy_denied_session_is_not_left_dangling(env, monkeypatch):
    """Отказ политики тоже закрывает строку: иначе она вечно висит в created."""
    import sqlalchemy as sa

    from bcc.features import browser as feat
    from bcc.v2.browser_control import BrowserPolicyDenied
    from bcc.v2.tables import browser_sessions as bs_t

    class _Denied:
        async def start(self, *a, **k):
            raise BrowserPolicyDenied("payment denied")

    monkeypatch.setattr(feat, "_mgr", lambda svc: _Denied())

    resp = await env.client.post("/api/browser/sessions", json={})
    assert resp.status_code == 403, resp.text
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(bs_t.c.status))).fetchall()
    assert all(r[0] == "failed" for r in rows), rows
