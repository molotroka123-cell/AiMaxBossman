"""MF-012: real Chromium + live BCC; transport fault injection is labelled.

No model-backed acceptance is claimed here. Error responses deliberately
exercise recovery, while project creation, edits, saves and restart use BCC.
Set BCC_REQUIRE_BROWSER=1 in CI: a missing browser must fail the gate.
"""
from __future__ import annotations

import json

import pytest

from .browser_support import click_in_preview, chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401
from .test_web_designer_apply_idempotent_ui import _code, _wait_code

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

BASE = '<!doctype html><html><body><h1>MF012 BASE</h1><p>tail</p></body></html>'
DRAFT = BASE.replace('MF012 BASE', 'OWNER DRAFT')


@pytest.fixture
def live(editor_server):
    # The same fresh-process server verifies source and installed-wheel modes;
    # installed acceptance forbids source imports and checks the embedded SHA.
    return editor_server


def _project(page, live):
    _login(page, live)
    page.goto(live.url + '/#/web_designer')
    page.get_by_placeholder('Название проекта, например «Кофейня Север»').fill('MF012 owner project')
    page.get_by_role('button', name='Открыть проект', exact=True).click()
    editor = page.locator('textarea.bd-code')
    editor.wait_for()
    pid = int(page.evaluate("localStorage.getItem('bd.lastProject')"))
    editor.fill(BASE)
    editor.press('Control+s')
    _wait_code(page, pid, lambda code: code == BASE)
    # The save reloads the preview. A click in the not-yet-reloaded document
    # selects an element the fresh frame then reports as lost (CI race on
    # 942d13b), so wait until the preview shows the saved code.
    page.frame_locator('iframe.bd-frame').locator('h1', has_text='MF012 BASE').wait_for()
    return pid


def _full(page, pid):
    return page.evaluate("""async pid => (await fetch('/api/web-designer/projects/' + pid,
      {credentials: 'include'})).json()""", pid)


def _replace_on_server(page, pid, code):
    return page.evaluate("""async ([pid, html]) => {
      const r = await fetch('/api/web-designer/projects/' + pid + '/code', {
        method: 'PUT', credentials: 'include', headers: {'Content-Type': 'application/json',
        'X-BCC-CSRF': localStorage.getItem('bcc.csrf')}, body: JSON.stringify({html})});
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    }""", [pid, code])


@pytest.mark.parametrize('status', [502, 403, 413, 404])
def test_save_error_is_visible_and_only_safe_retry_is_offered(live, status):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            pid = _project(page, live)
            before = _full(page, pid)['meta']['version']
            requests = []

            def failed_save(route):
                requests.append(route.request.post_data_json)
                route.fulfill(status=status, content_type='application/json', body=json.dumps(
                    {'error': {'message': f'MF012 precise failure {status}'}}))

            pattern = f'**/api/web-designer/projects/{pid}/code'
            page.route(pattern, failed_save)
            page.locator('textarea.bd-code').fill(DRAFT)
            page.locator('textarea.bd-code').press('Control+s')
            panel = page.get_by_test_id('bd-recovery')
            expect(panel).to_contain_text(f'HTTP {status}')
            expect(panel).to_contain_text(f'MF012 precise failure {status}')
            expect(panel).to_contain_text('Сохранённое состояние обновлено')
            assert page.locator('textarea.bd-code').input_value() == DRAFT
            assert _code(page, pid) == BASE
            retry = panel.get_by_role('button', name='Повторить безопасно')
            if status == 502:
                expect(retry).to_be_visible()
                page.unroute(pattern, failed_save)
                retry.click()
                _wait_code(page, pid, lambda code: code == DRAFT)
                assert _full(page, pid)['meta']['version'] == before + 1
                expect(panel).to_be_hidden()
            else:
                expect(retry).to_have_count(0)
                panel.get_by_role('button', name='Обновить состояние').click()
                expect(panel).to_contain_text(f'MF012 precise failure {status}')
                assert len(requests) == 1, 'read-only recovery must not replay the refused write'
                assert _full(page, pid)['meta']['version'] == before
            assert errors == []
        finally:
            browser.close()


def test_conflict_preserves_draft_blocks_generate_and_requires_review(live):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            pid = _project(page, live)
            other = BASE.replace('MF012 BASE', 'OTHER TAB')
            _replace_on_server(page, pid, other)
            page.locator('textarea.bd-code').fill(DRAFT)
            # The generation button flushes the draft. Its real 409 must stop
            # generation instead of overwriting the other tab and the draft.
            generated = []
            page.on('request', lambda request: generated.append(request.url)
                    if request.url.endswith('/generate') else None)
            page.get_by_role('button', name='Сгенерировать сайт', exact=True).click()
            panel = page.get_by_test_id('bd-recovery')
            expect(panel).to_contain_text('HTTP 409')
            expect(panel).to_contain_text('Автосохранение приостановлено')
            assert generated == []
            assert _code(page, pid) == other
            assert page.locator('textarea.bd-code').input_value() == DRAFT
            page.get_by_role('button', name='+ Проект', exact=True).click()
            expect(page.locator('textarea.bd-code')).to_have_value(DRAFT)
            panel.locator('summary').click()
            expect(page.get_by_label('Сохранённый код для сравнения')).to_have_value(other)
            panel.get_by_role('button', name='Сохранить мой код поверх').click()
            page.get_by_role('button', name='Сохранить мой код', exact=True).click()
            _wait_code(page, pid, lambda code: code == DRAFT)
            expect(panel).to_be_hidden()
        finally:
            browser.close()


def test_unknown_model_outcome_never_exposes_retry(live):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            pid = _project(page, live)
            click_in_preview(page, 'h1')
            prompt = page.get_by_placeholder('Например: сделай кнопку заметнее и добавь тень')
            prompt.fill('Make this heading green')
            requests = []

            def unknown(route):
                requests.append(route.request.post_data_json)
                route.fulfill(status=502, content_type='application/json',
                              body=json.dumps({'detail': 'provider outcome unknown'}))

            page.route(f'**/api/web-designer/projects/{pid}/ai-edit', unknown)
            page.get_by_role('button', name='Спросить модель').click()
            panel = page.get_by_test_id('bd-recovery')
            expect(panel).to_contain_text('повторное обращение может снова списать средства')
            expect(panel.get_by_role('button', name='Повторить безопасно')).to_have_count(0)
            panel.get_by_role('button', name='Обновить состояние').click()
            assert len(requests) == 1
            assert _code(page, pid) == BASE
        finally:
            browser.close()


def test_project_edit_apply_twice_model_selection_and_restart(live):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            pid = _project(page, live)
            # Registry metadata is real; no call to this unconfigured model.
            model = page.evaluate("""async () => {
              const headers = {'Content-Type': 'application/json',
                'X-BCC-CSRF': localStorage.getItem('bcc.csrf')};
              const p = await fetch('/api/providers', {method: 'POST', headers,
                body: JSON.stringify({name: 'MF012 local metadata', kind: 'openai_compat',
                  base_url: 'http://127.0.0.1:11434/v1', api_key: ''})});
              if (!p.ok) throw new Error(await p.text());
              const provider = await p.json();
              const m = await fetch('/api/models', {method: 'POST', headers,
                body: JSON.stringify({provider_id: provider.id, name: 'owner-local', alias: 'Owner local'})});
              if (!m.ok) throw new Error(await m.text());
              return m.json();
            }""")
            page.reload()
            page.locator('iframe.bd-frame').wait_for()
            click_in_preview(page, 'h1')
            selector = page.get_by_label('Модель для AI-правки')
            selector.select_option(str(model['id']))
            row = page.locator('div.bd-row', has_text='Текст').first
            row.locator('input[type=text]').fill('APPLIED OWNER HEADING')
            row.get_by_role('button', name='Применить').click()
            _wait_code(page, pid, lambda code: 'APPLIED OWNER HEADING' in code)
            expect(row.locator('input[type=text]')).to_have_value('APPLIED OWNER HEADING')
            row.get_by_role('button', name='Применить').click()
            _wait_code(page, pid, lambda code: 'APPLIED OWNER HEADING' in code)
            live.restart()
            page.reload()
            page.locator('iframe.bd-frame').wait_for()
            expect(page.frame_locator('iframe.bd-frame').locator('h1')).to_have_text('APPLIED OWNER HEADING')
            click_in_preview(page, 'h1')
            expect(page.get_by_label('Модель для AI-правки')).to_have_value(str(model['id']))
            assert 'APPLIED OWNER HEADING' in _code(page, pid)
            assert errors == []
        finally:
            browser.close()
