"""Real UI/backend regressions for navigation approval and session handling."""
import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_human_navigation_uses_policy_without_self_approval(live, monkeypatch):
    from playwright.sync_api import sync_playwright

    # Only the isolated test server is used as a navigation destination.
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")
    target = live.url + "/?browser-navigation-regression=1"
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.goto(live.url + "/#/browser")
            page.get_by_role("button", name="Новое окно", exact=True).click()
            address = page.locator("input[placeholder='https://…']")
            address.wait_for()
            # Wait for the initial state poll before typing the target.
            page.get_by_role("button", name="Взять управление", exact=True).wait_for()
            address.fill(target)
            with page.expect_response(lambda r: r.url.endswith("/act")) as response:
                page.get_by_role("button", name="Перейти", exact=True).click()
            result = response.value
            assert result.status == 200, result.text()
            body = result.request.post_data_json
            assert body["actor"] == "human"
            assert "approved" not in body
            assert result.json()["url"] == target
            assert page.locator("#shell").is_visible()
        finally:
            browser.close()


def test_policy_403_keeps_session_but_401_requires_login(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            result = page.evaluate("""async () => {
              const {api, UNAUTHORIZED_EVENT} = await import('/api.js');
              let events = 0;
              window.addEventListener(UNAUTHORIZED_EVENT, () => events++);
              let denied;
              try {
                await api.raw('/api/browser/sessions/0/act', {method: 'POST',
                  body: {action: 'navigate', url: 'https://example.com', approved: true}});
              } catch (e) { denied = {status: e.status, isAuth: e.isAuth}; }
              return {denied, events, csrf: Boolean(localStorage.getItem('bcc.csrf'))};
            }""")
            assert result == {"denied": {"status": 403, "isAuth": False},
                              "events": 0, "csrf": True}
            assert page.locator("#shell").is_visible()
            # A real session invalidation must still trigger authentication handling.
            result = page.evaluate("""async () => {
              const {api, UNAUTHORIZED_EVENT} = await import('/api.js');
              await api.logout();
              let events = 0;
              window.addEventListener(UNAUTHORIZED_EVENT, () => events++);
              try { await api.system(); }
              catch (e) { return {status: e.status, isAuth: e.isAuth, events}; }
            }""")
            assert result == {"status": 401, "isAuth": True, "events": 1}
            page.locator("#login-submit").wait_for(state="visible")
        finally:
            browser.close()


def test_lost_csrf_token_requires_login_instead_of_dead_403(live):
    """Журнал тестового периода 51307af16b90: POST /api/providers и /api/browser/.../act
    отвечали 403 за 0 мс — cookie сессии жива, а CSRF-токен вкладки потерян (другой вход,
    очищенное хранилище). Для владельца это «кнопка не работает». Теперь такой 403 несёт
    code=csrf, UI считает его auth-ошибкой и показывает форму входа; политический 403
    (см. тест выше) сессию по-прежнему не трогает."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            result = page.evaluate("""async () => {
              const {api, UNAUTHORIZED_EVENT} = await import('/api.js');
              localStorage.setItem('bcc.csrf', 'stale-token-from-another-login');
              let events = 0;
              window.addEventListener(UNAUTHORIZED_EVENT, () => events++);
              try { await api.createProvider({name: 'x', provider_kind: 'openai_compat', base_url: 'http://127.0.0.1:9'}); }
              catch (e) { return {status: e.status, code: e.code, isAuth: e.isAuth, events,
                                  csrf: localStorage.getItem('bcc.csrf')}; }
              return {unexpected: true};
            }""")
            assert result == {"status": 403, "code": "csrf", "isAuth": True, "events": 1, "csrf": None}
            page.locator("#login-submit").wait_for(state="visible")
        finally:
            browser.close()
