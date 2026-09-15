"""Real UI/HTTP/SQLite regression. No live provider or GitHub publish is claimed."""
import time
import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_cc_ux_p1_findings import ORDER, _agents, _home, _srv_client, _start, _type_order
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def test_stale_agent_refusal_keeps_owner_text_and_creates_no_dead_task(live):
    from playwright.sync_api import sync_playwright
    ids = _agents(live, ['Исполнитель'])
    with sync_playwright() as pw:
        browser, page = _home(pw, live, [])
        try:
            _type_order(page)
            with _srv_client(live) as client:
                client.patch(f'/api/agents/{ids[0]}', json={'enabled': False})
            _start(page)
            page.wait_for_selector('.toast-err, .toast-error', timeout=10000)
            assert page.input_value('.bx-command-input') == ORDER
            with _srv_client(live) as client:
                assert client.get('/api/tasks').json() == []
                client.patch(f'/api/agents/{ids[0]}', json={'enabled': True})
            _start(page)
            page.wait_for_function("document.querySelector('.bx-command-input').value === ''")
            with _srv_client(live) as client:
                rows = client.get('/api/tasks').json()
                assert len(rows) == 1
                assert rows[0]['agent_id'] == ids[0] and rows[0]['status'] == 'queued'
        finally:
            browser.close()


def test_overview_never_calls_unconfigured_http_200_healthy(live):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            page.goto(live.url + '/#/overview')
            line = page.locator('#view .statusline')
            line.wait_for()
            assert 'система в норме' not in line.inner_text()
            assert 'ненастроенные компоненты' in line.inner_text()
            page.click('#think-open')
            page.locator('#think-pane').wait_for(state='visible')
            page.click('#think-close')
            page.locator('#think-pane').wait_for(state='hidden')
        finally:
            browser.close()


@pytest.mark.parametrize('width', [1440, 390])
def test_publish_is_busy_then_shows_refusal_without_retry_or_false_success(live, width):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900})
            _login(page, live)
            pending = []
            page.route('**/api/testing/publish', lambda route: pending.append(route))
            button = page.locator('#bcc-testing-publish')
            button.click()
            page.wait_for_function("document.querySelector('#bcc-testing-publish').disabled")
            assert button.get_attribute('aria-busy') == 'true'
            assert button.inner_text() == 'Отправляю…'
            deadline = time.monotonic() + 10
            while not pending and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert len(pending) == 1
            pending[0].fulfill(status=409, content_type='application/json',
                               body='{"error":{"message":"публикация запрещена политикой"}}')
            page.get_by_text('ошибка публикации: публикация запрещена политикой', exact=False).wait_for()
            assert button.is_enabled()
            assert len(pending) == 1
        finally:
            browser.close()
